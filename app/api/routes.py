"""Endpoints de serviço consumidos pelos agentes e pelo workflow do n8n."""

import logging

from fastapi import APIRouter, Request

from app.agents.pipeline import executar_pipeline_analise
from app.core.errors import DomainError
from app.core.schemas import (
    CasoEntrada,
    CasoResultado,
    ConsultaRAG,
    ExecucaoEntrada,
    Pedido,
    Reembolso,
    ResultadoAnalise,
    ResultadoRAG,
    ValidacaoEntrada,
)
from app.core.validator import validar

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health")
def health(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "mock_embeddings": settings.mock_embeddings,
        "data_referencia": settings.now().date().isoformat(),
        "limite_alcada_automatica": str(settings.limite_alcada_automatica),
    }


@router.get("/pedidos/{pedido_id}", response_model=Pedido)
def get_order(pedido_id: str, request: Request) -> Pedido:
    return request.app.state.database.get_order(pedido_id)


@router.post("/rag/consultar", response_model=ResultadoRAG)
def search_rag(payload: ConsultaRAG, request: Request) -> ResultadoRAG:
    try:
        citations = request.app.state.retriever.search(payload.query, payload.top_k)
    except Exception as exc:
        logger.exception("Falha na consulta ao índice vetorial")
        raise DomainError(
            502, "Busca vetorial indisponível; nenhuma execução foi autorizada."
        ) from exc
    return ResultadoRAG(
        trechos=citations,
        modo="mock" if request.app.state.settings.mock_embeddings else "gemini",
    )


@router.post("/validar", response_model=CasoResultado)
def validate_case(payload: ValidacaoEntrada, request: Request) -> CasoResultado:
    if not request.app.state.settings.permitir_validar_externo:
        raise DomainError(
            403,
            "POST /validar está desativado; use POST /casos/analisar "
            "(ou PERMITIR_VALIDAR_EXTERNO=true).",
        )
    database = request.app.state.database
    existing = database.get_case(payload)
    if existing is not None:
        return existing
    settings = request.app.state.settings
    order = database.get_order(payload.caso.pedido_id)
    received = settings.now()
    validation = validar(
        payload,
        order,
        received,
        settings.limite_alcada_automatica,
        request.app.state.retriever.valid,
    )
    result = CasoResultado(
        case_id=payload.caso.case_id,
        pedido_id=payload.caso.pedido_id,
        valor_solicitado=payload.caso.valor_solicitado,
        recebido_em=received,
        modo="mock" if settings.mock_embeddings else "gemini",
        evidencias=payload.evidencias,
        politicas=payload.politicas,
        coordenador=payload.coordenador,
        validador=validation,
        autorizado_execucao=validation.autorizado_execucao,
        motivo_bloqueio=validation.motivo_bloqueio,
    )
    return database.save_case(payload, result)


@router.post("/reembolsos/executar", response_model=Reembolso)
def execute_refund(payload: ExecucaoEntrada, request: Request) -> Reembolso:
    return request.app.state.database.execute(payload, request.app.state.retriever.valid)


@router.post("/casos/analisar", response_model=ResultadoAnalise)
def analyze_case(payload: CasoEntrada, request: Request) -> ResultadoAnalise:
    return executar_pipeline_analise(
        payload, request.app.state.database, retriever=request.app.state.retriever
    )
