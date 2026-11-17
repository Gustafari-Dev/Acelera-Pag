"""Orquestra os três agentes em sequência e sempre passa pelo validador Python."""

import hashlib
import json
from collections.abc import Callable
from time import perf_counter
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel

from app.agents.gemini import GeminiAgents
from app.agents.prompts import PROMPTS, VERSAO_PROMPTS
from app.core.errors import DomainError
from app.core.schemas import (
    CasoEntrada,
    CasoResultado,
    MetadadosAnalise,
    ParecerCoordenador,
    ParecerEvidencias,
    ParecerPoliticas,
    RastroEtapa,
    ResultadoAnalise,
    ValidacaoEntrada,
)
from app.core.validator import validar_caso
from app.db.database import Database as Session
from app.rag.retriever import PolicyRetriever
from scripts.mock_agents import analisar_evidencias, analisar_politicas, coordenar_caso

Value = TypeVar("Value")


def digest(value) -> str:
    def encode(item):
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        raise TypeError(f"Tipo não serializável no rastro: {type(item).__name__}")

    content = json.dumps(value, default=encode, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def executar_pipeline_analise(
    caso: CasoEntrada, db: Session, *, retriever: PolicyRetriever | None = None
) -> ResultadoAnalise:
    """Session é o repositório SQLite existente; nenhum ORM ou framework de agentes é usado."""
    existing = db.get_analysis(caso)
    if existing is not None:
        return existing
    settings = db.settings
    started = settings.now()
    timer = perf_counter()
    traces: list[RastroEtapa] = []

    def step(name: str, inputs, operation: Callable[[], Value]) -> Value:
        start = perf_counter()
        output = operation()
        traces.append(
            RastroEtapa(
                etapa=name,
                duracao_ms=round((perf_counter() - start) * 1000, 3),
                entrada_sha256=digest(inputs),
                saida_sha256=digest(output),
            )
        )
        return output

    order = step(
        "pedido_oficial", {"pedido_id": caso.pedido_id}, lambda: db.get_order(caso.pedido_id)
    )
    days = (started.date() - order.data_entrega).days
    query = f"{caso.motivo.value}; {days} dias desde a entrega; {caso.relato[:1600]}"

    def retrieve():
        try:
            source = retriever or PolicyRetriever(settings)
            # O corpus é pequeno: recupera até 20 seções para não omitir norma obrigatória.
            excerpts = source.search(query, k=20)
            return source, excerpts
        except Exception as exc:
            raise DomainError(502, "RAG indisponível; análise não autorizada.") from exc

    rag_timer = perf_counter()
    source, excerpts = retrieve()
    traces.append(
        RastroEtapa(
            etapa="rag",
            duracao_ms=round((perf_counter() - rag_timer) * 1000, 3),
            entrada_sha256=digest({"query": query}),
            saida_sha256=digest(excerpts),
        )
    )
    evidence_context = {
        "caso": caso.model_dump(mode="json"),
        "pedido_oficial": order.model_dump(mode="json"),
    }
    policy_context = {
        "motivo": caso.motivo.value,
        "dias_desde_entrega": days,
        "compra_online": order.compra_online,
        "trechos": [excerpt.model_dump(mode="json") for excerpt in excerpts],
    }
    llm = None if settings.mock_llm else GeminiAgents(settings)
    try:
        evidence = step(
            "analista_evidencias",
            evidence_context,
            lambda: (
                analisar_evidencias(caso, order)
                if llm is None
                else llm.generate(
                    "evidencias", PROMPTS["evidencias"], evidence_context, ParecerEvidencias
                )
            ),
        )
        policies = step(
            "analista_politicas",
            policy_context,
            lambda: (
                analisar_politicas(caso, order, excerpts)
                if llm is None
                else llm.generate(
                    "politicas", PROMPTS["politicas"], policy_context, ParecerPoliticas
                )
            ),
        )
        coordinator_context = {
            **evidence_context,
            "parecer_evidencias": evidence.model_dump(mode="json"),
            "parecer_politicas": policies.model_dump(mode="json"),
            "limite_alcada": str(settings.limite_alcada_automatica),
        }
        coordinator = step(
            "coordenador_caso",
            coordinator_context,
            lambda: (
                coordenar_caso(caso, order, evidence, policies, settings.limite_alcada_automatica)
                if llm is None
                else llm.generate(
                    "coordenador", PROMPTS["coordenador"], coordinator_context, ParecerCoordenador
                )
            ),
        )
    finally:
        if llm is not None:
            llm.close()

    envelope = ValidacaoEntrada(
        caso=caso,
        pedido_informado=order,
        evidencias=evidence,
        politicas=policies,
        coordenador=coordinator,
    )
    current_order = db.get_order(caso.pedido_id)
    validation = step(
        "validador_deterministico",
        {"envelope": envelope, "pedido": current_order},
        lambda: validar_caso(
            envelope,
            current_order,
            started,
            settings.limite_alcada_automatica,
            lambda citation: citation in excerpts and source.valid(citation),
        ),
    )
    mode_reason = None
    if settings.mock_llm:
        mode_reason = "USE_MOCK_LLM=true" if settings.use_mock_llm else "GOOGLE_API_KEY ausente"
    result = ResultadoAnalise(
        caso=caso,
        pedido_oficial=order,
        parecer_evidencias=evidence,
        parecer_politicas=policies,
        parecer_coordenador=coordinator,
        validacao=validation,
        autorizado_execucao=validation.autorizado_execucao,
        motivo_bloqueio=validation.motivo_bloqueio,
        metadados=MetadadosAnalise(
            analise_id=uuid4(),
            modo="mock" if settings.mock_llm else "gemini",
            motivo_modo_mock=mode_reason,
            modelo_usado="mock-deterministico-v1" if settings.mock_llm else settings.gemini_model,
            modo_embeddings="mock" if settings.mock_embeddings else "gemini",
            iniciado_em=started,
            concluido_em=settings.now(),
            tempo_total_ms=round((perf_counter() - timer) * 1000, 3),
            versao_pipeline=f"v1/prompts-{VERSAO_PROMPTS}",
            prompts_sha256={
                name: hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                for name, prompt in PROMPTS.items()
            },
            rastreabilidade=traces,
        ),
    )
    authorization = CasoResultado(
        case_id=caso.case_id,
        pedido_id=caso.pedido_id,
        valor_solicitado=caso.valor_solicitado,
        recebido_em=started,
        modo=result.metadados.modo,
        evidencias=evidence,
        politicas=policies,
        coordenador=coordinator,
        validador=validation,
        autorizado_execucao=validation.autorizado_execucao,
        motivo_bloqueio=validation.motivo_bloqueio,
    )
    return db.save_analysis(envelope, authorization, result)
