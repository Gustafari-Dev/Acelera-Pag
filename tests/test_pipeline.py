"""Novo endpoint com quatro cenários, parsing real do SDK e travas obrigatórias."""

import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google import genai
from google.genai import Client as OfficialGenaiClient
from google.genai import errors, types
from pydantic import SecretStr

from app.agents import gemini as gemini_module
from app.agents.gemini import SCHEMAS_LLM, GeminiAgents
from app.agents.pipeline import hidratar_citacoes
from app.agents.prompts import PROMPTS
from app.core.schemas import (
    CasoEntrada,
    Citacao,
    ParecerCoordenador,
    ParecerEvidencias,
    ParecerPoliticas,
    Pedido,
    Recomendacao,
    ReferenciaCitacao,
    RespostaPoliticas,
    ResultadoAnalise,
)
from app.main import create_app
from scripts.mock_agents import analisar_evidencias, analisar_politicas, coordenar_caso
from tests.helpers import payload_for

EXPECTED = [
    ("PED-001", "APROVAR", True),
    ("PED-002", "REJEITAR", False),
    ("PED-003", "REVISAO_HUMANA", False),
    ("PED-004", "APROVAR", True),
]


def case_for(client, order_id="PED-001", **overrides):
    if order_id == "PED-003":
        overrides = {"valor_solicitado": "999.00", "anexo_status": "ILEGIVEL", **overrides}
    return payload_for(client, order_id, **overrides).caso.model_dump(mode="json")


def execute_for(case):
    return {
        "case_id": case["case_id"],
        "pedido_id": case["pedido_id"],
        "valor": case["valor_solicitado"],
    }


def assert_result(client, case, response, decision, authorized):
    assert response.status_code == 200, response.text
    result = ResultadoAnalise.model_validate(response.json())
    assert result.parecer_coordenador.recomendacao.value == decision
    assert result.autorizado_execucao is authorized
    assert result.validacao.autorizado_execucao is authorized
    assert result.motivo_bloqueio == result.validacao.motivo_bloqueio
    assert result.parecer_evidencias is not None
    assert result.parecer_politicas.citacoes
    assert result.pedido_oficial.pedido_id == case["pedido_id"]
    assert result.metadados.tempo_total_ms >= 0
    assert [step.etapa for step in result.metadados.rastreabilidade] == [
        "pedido_oficial",
        "rag",
        "analista_evidencias",
        "analista_politicas",
        "coordenador_caso",
        "validador_deterministico",
    ]
    assert all(len(step.saida_sha256) == 64 for step in result.metadados.rastreabilidade)
    assert len(result.metadados.prompts_sha256) == 3
    execution = client.post("/reembolsos/executar", json=execute_for(case))
    if authorized:
        if case["pedido_id"] == "PED-004":
            assert execution.status_code == 503
            execution = client.post("/reembolsos/executar", json=execute_for(case))
        assert execution.status_code == 200, execution.text
        repeated = client.post("/reembolsos/executar", json=execute_for(case))
        assert repeated.json() == execution.json()
    else:
        assert execution.status_code == (409 if case["pedido_id"] == "PED-002" else 403)
    return result


@pytest.mark.parametrize("order_id,decision,authorized", EXPECTED)
def test_analyze_endpoint_mock_all_scenarios(client, order_id, decision, authorized):
    case = case_for(client, order_id)
    response = client.post("/casos/analisar", json=case)
    result = assert_result(client, case, response, decision, authorized)
    assert result.metadados.modo == "mock"
    assert result.metadados.modelo_usado == "mock-deterministico-v1"


def as_reference(parecer: ParecerPoliticas) -> RespostaPoliticas:
    """O que o LLM devolve: o parecer sem trecho/sha256 nas citações."""
    return RespostaPoliticas(
        dias_desde_entrega=parecer.dias_desde_entrega,
        elegivel_automatico=parecer.elegivel_automatico,
        fundamentacao=parecer.fundamentacao,
        citacoes=[
            ReferenciaCitacao(
                documento=item.documento,
                versao=item.versao,
                secao=item.secao,
                relevancia="Norma aplicável ao motivo.",
            )
            for item in parecer.citacoes
        ],
    )


def install_gemini_double(monkeypatch, configuration, representation="model", transform=None):
    calls, clients = [], []
    configuration.use_mock_llm = False
    configuration.google_api_key = SecretStr("credencial-ficticia-sem-rede")

    class ClientDouble:
        def __init__(self, **kwargs):
            self.options, self.models, self.closed = kwargs, self, False
            self.case, self.order = None, None
            # Evidências e Políticas rodam em paralelo; Políticas espera o caso decodificado.
            self.case_ready = threading.Event()
            clients.append(self)

        def generate_content(self, **kwargs):
            calls.append(kwargs)
            llm_schema = kwargs["config"].response_schema
            schema = next(domain for domain, llm in SCHEMAS_LLM.items() if llm is llm_schema)
            context = json.loads(kwargs["contents"])
            if schema is ParecerEvidencias:
                self.case = CasoEntrada.model_validate(context["caso"])
                self.order = Pedido.model_validate(context["pedido_oficial"])
                self.case_ready.set()
                output = analisar_evidencias(self.case, self.order)
            elif schema is RespostaPoliticas:
                assert self.case_ready.wait(5)
                output = as_reference(
                    analisar_politicas(
                        self.case,
                        self.order,
                        [Citacao.model_validate(item) for item in context["trechos"]],
                    )
                )
            elif schema is ParecerCoordenador:
                output = coordenar_caso(
                    self.case,
                    self.order,
                    ParecerEvidencias.model_validate(context["parecer_evidencias"]),
                    ParecerPoliticas.model_validate(context["parecer_politicas"]),
                    configuration.limite_alcada_automatica,
                )
            else:
                raise AssertionError(f"Schema inesperado: {schema}")
            if transform:
                changed = transform(schema, output)
                if changed is not None:
                    return changed
            if representation == "text":
                return types.GenerateContentResponse(
                    candidates=[
                        types.Candidate(
                            finish_reason=types.FinishReason.STOP,
                            content=types.Content(
                                parts=[types.Part.from_text(text=output.model_dump_json())]
                            ),
                        )
                    ]
                )
            parsed = output if representation == "model" else output.model_dump(mode="json")
            if representation == "dict":
                return SimpleNamespace(
                    parsed=parsed,
                    candidates=[types.Candidate(finish_reason=types.FinishReason.STOP)],
                )
            return types.GenerateContentResponse(
                parsed=parsed, candidates=[types.Candidate(finish_reason=types.FinishReason.STOP)]
            )

        def close(self):
            self.closed = True

    monkeypatch.setattr(genai, "Client", ClientDouble)
    return calls, clients


@pytest.mark.parametrize("order_id,decision,authorized", EXPECTED)
@pytest.mark.parametrize("representation", ["model", "dict", "text"])
def test_gemini_schemas_and_endpoint_all_scenarios(
    configuration, monkeypatch, order_id, decision, authorized, representation
):
    calls, clients = install_gemini_double(monkeypatch, configuration, representation)
    with TestClient(create_app(configuration)) as client:
        case = case_for(client, order_id)
        response = client.post("/casos/analisar", json=case)
        result = assert_result(client, case, response, decision, authorized)
        assert result.metadados.modo == "gemini"
        assert result.metadados.modelo_usado == configuration.gemini_model
        assert len(calls) == 3
        by_schema = {call["config"].response_schema: call for call in calls}
        # As duas primeiras são concorrentes (ordem livre); o Coordenador é sempre o último.
        assert calls[-1]["config"].response_schema is SCHEMAS_LLM[ParecerCoordenador]
        for name, domain in zip(
            PROMPTS, (ParecerEvidencias, RespostaPoliticas, ParecerCoordenador), strict=True
        ):
            call = by_schema[SCHEMAS_LLM[domain]]
            assert call["config"].system_instruction == PROMPTS[name]
            assert call["config"].response_mime_type == "application/json"
            assert call["config"].thinking_config is None
        etapas = [trace.etapa for trace in result.metadados.rastreabilidade]
        assert etapas.index("analista_evidencias") < etapas.index("analista_politicas")
        assert clients[0].closed
        assert clients[0].options["http_options"].timeout == 30000
        repeat = client.post("/casos/analisar", json=case)
        assert repeat.json() == response.json()
        assert len(calls) == 3


def test_missing_key_falls_back_even_with_both_flags_false(configuration):
    configuration.use_mock_llm = False
    configuration.use_mock_embeddings = False
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        response = client.post("/casos/analisar", json=case)
        assert response.status_code == 200
        assert response.json()["autorizado_execucao"]
        assert response.json()["metadados"]["modo"] == "mock"
        assert response.json()["metadados"]["modo_embeddings"] == "mock"
        assert response.json()["metadados"]["motivo_modo_mock"] == "GOOGLE_API_KEY ausente"


@pytest.mark.parametrize(
    "bad_response",
    [
        SimpleNamespace(parsed=None, text="{JSON inválido"),
        SimpleNamespace(parsed=None, text=""),
        SimpleNamespace(parsed={"vinculo_valido": True}, text=None),
        SimpleNamespace(parsed=None, text="null"),
    ],
)
def test_invalid_gemini_responses_fail_without_authorization(
    configuration, monkeypatch, bad_response
):
    calls, clients = install_gemini_double(
        monkeypatch, configuration, transform=lambda schema, output: bad_response
    )
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        response = client.post("/casos/analisar", json=case)
        assert response.status_code == 502
        # Evidências e Políticas são disparadas juntas; o Coordenador nunca é chamado.
        assert len(calls) == 2
        assert clients[0].closed
        assert client.post("/reembolsos/executar", json=execute_for(case)).status_code == 403
        with client.app.state.database.connection() as connection:
            assert connection.execute("SELECT count(*) FROM casos").fetchone()[0] == 0


@pytest.mark.parametrize("reason", [types.FinishReason.SAFETY, types.FinishReason.MAX_TOKENS])
def test_interrupted_generation_never_authorizes(configuration, monkeypatch, reason):
    install_gemini_double(
        monkeypatch,
        configuration,
        transform=lambda schema, output: SimpleNamespace(
            parsed=output, candidates=[types.Candidate(finish_reason=reason)]
        ),
    )
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        assert client.post("/casos/analisar", json=case).status_code == 502
        assert client.post("/reembolsos/executar", json=execute_for(case)).status_code == 403


def test_gemini_network_error_does_not_silently_switch_to_mock(configuration, monkeypatch):
    def fail(schema, output):
        raise TimeoutError("Sem rede no teste")

    _, clients = install_gemini_double(monkeypatch, configuration, transform=fail)
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        assert client.post("/casos/analisar", json=case).status_code == 502
        assert client.post("/reembolsos/executar", json=execute_for(case)).status_code == 403
        assert clients[0].closed


def test_invented_citation_blocks_even_if_gemini_coordinator_approves(configuration, monkeypatch):
    def forge(schema, output):
        if schema is RespostaPoliticas:
            output.citacoes.append(
                ReferenciaCitacao(
                    documento="cdc_artigos_selecionados",
                    versao="v1",
                    secao="Seção 9.9",
                    relevancia="Norma criada pelo modelo.",
                )
            )
        if schema is ParecerCoordenador:
            output.recomendacao = Recomendacao.APROVAR

    install_gemini_double(monkeypatch, configuration, transform=forge)
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        response = client.post("/casos/analisar", json=case)
        assert response.status_code == 200
        assert not response.json()["autorizado_execucao"]
        assert "citacao_normativa_valida" in response.json()["motivo_bloqueio"]
        politicas = response.json()["parecer_politicas"]
        assert politicas["citacoes_rejeitadas"] == ["cdc_artigos_selecionados/v1/Seção 9.9"]
        assert all(item["secao"] != "Seção 9.9" for item in politicas["citacoes"])
        assert client.post("/reembolsos/executar", json=execute_for(case)).status_code == 403


def test_name_is_compared_by_evidence_and_deterministic_validator(client):
    case = case_for(client, cliente_nome="Outro Titular")
    response = client.post("/casos/analisar", json=case)
    assert response.status_code == 200
    assert "cliente_nome" in response.json()["parecer_evidencias"]["divergencias"]
    assert "nome_informado_confere" in response.json()["motivo_bloqueio"]


def test_optional_name_normalization_is_compatible_with_old_payloads(client):
    case = case_for(client, cliente_nome="  ÁNA   demo ")
    response = client.post("/casos/analisar", json=case)
    assert response.status_code == 200
    assert response.json()["autorizado_execucao"]


def test_analysis_idempotency_and_conflicting_input(client):
    case = case_for(client)
    first = client.post("/casos/analisar", json=case)
    assert first.status_code == 200
    assert client.post("/casos/analisar", json=case).json() == first.json()
    case["relato"] = "Novo relato com o mesmo UUID."
    assert client.post("/casos/analisar", json=case).status_code == 409


def test_unknown_order_returns_404(client):
    case = case_for(client)
    case["pedido_id"] = "PED-999"
    assert client.post("/casos/analisar", json=case).status_code == 404


def test_rag_failure_never_authorizes(client, monkeypatch):
    case = case_for(client)

    def unavailable(*args, **kwargs):
        raise RuntimeError("Índice indisponível")

    monkeypatch.setattr(client.app.state.retriever, "search", unavailable)
    assert client.post("/casos/analisar", json=case).status_code == 502
    assert client.post("/reembolsos/executar", json=execute_for(case)).status_code == 403


def test_no_legacy_case_can_be_overwritten_by_pipeline(client):
    envelope = payload_for(client)
    assert client.post("/validar", json=envelope.model_dump(mode="json")).status_code == 200
    assert (
        client.post("/casos/analisar", json=envelope.caso.model_dump(mode="json")).status_code
        == 409
    )


def test_response_contract_rejects_extra_fields(client):
    case = case_for(client)
    case["autorizado_execucao"] = True
    assert client.post("/casos/analisar", json=case).status_code == 422


def test_pipeline_restart_preserves_analysis_and_execution(client, configuration):
    case = case_for(client, "PED-004")
    first = client.post("/casos/analisar", json=case)
    assert first.status_code == 200
    assert client.post("/reembolsos/executar", json=execute_for(case)).status_code == 503
    with TestClient(create_app(configuration)) as restarted:
        assert restarted.post("/casos/analisar", json=case).json() == first.json()
        assert restarted.post("/reembolsos/executar", json=execute_for(case)).status_code == 200


def test_official_sdk_serializes_all_pydantic_schemas_and_parses_network_json(
    client, configuration, monkeypatch
):
    envelope = payload_for(client)
    expected = [envelope.evidencias, as_reference(envelope.politicas), envelope.coordenador]
    official = OfficialGenaiClient(api_key="chave-ficticia-do-transporte", vertexai=False)
    requests = []

    def fake_request(method, path, body, http_options=None):
        output = expected[len(requests)]
        requests.append((method, path, body))
        return SimpleNamespace(
            headers={"content-type": "application/json"},
            body=json.dumps(
                {
                    "candidates": [
                        {
                            "finishReason": "STOP",
                            "content": {
                                "role": "model",
                                "parts": [{"text": output.model_dump_json()}],
                            },
                        }
                    ]
                }
            ),
        )

    monkeypatch.setattr(official._api_client, "request", fake_request)
    monkeypatch.setattr(genai, "Client", lambda **kwargs: official)
    adapter = GeminiAgents(configuration)
    try:
        for name, output in zip(PROMPTS, expected, strict=True):
            result = adapter.generate(name, PROMPTS[name], {"contexto": "teste"}, type(output))
            assert result == output
        assert len(requests) == 3
        for method, path, body in requests:
            assert method == "post"
            assert path.endswith(":generateContent")
            assert body["generationConfig"]["responseMimeType"] == "application/json"
            response_schema = body["generationConfig"].get("responseSchema")
            assert response_schema
            # A Gemini Developer API responde 400 INVALID_ARGUMENT a estes campos.
            serialized = json.dumps(response_schema)
            for unsupported in ("additional_properties", "additionalProperties", "anyOf"):
                assert unsupported not in serialized
    finally:
        adapter.close()


@pytest.mark.parametrize("stage", [RespostaPoliticas, ParecerCoordenador])
def test_invalid_later_stage_also_prevents_persisted_authorization(
    configuration, monkeypatch, stage
):
    def invalid(schema, output):
        if schema is stage:
            return SimpleNamespace(parsed={"campo_desconhecido": True}, text=None)

    _, clients = install_gemini_double(monkeypatch, configuration, transform=invalid)
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        assert client.post("/casos/analisar", json=case).status_code == 502
        assert client.post("/reembolsos/executar", json=execute_for(case)).status_code == 403
        assert clients[0].closed


def test_api_error_is_logged_with_status_but_never_secret_or_content(
    configuration, monkeypatch, caplog
):
    def reject(schema, output):
        raise errors.ClientError(
            400,
            {
                "error": {
                    "code": 400,
                    "status": "INVALID_ARGUMENT",
                    "message": "Chave credencial-ficticia-sem-rede rejeitada no response_schema.",
                }
            },
        )

    _, clients = install_gemini_double(monkeypatch, configuration, transform=reject)
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        with caplog.at_level("WARNING", logger="app.agents.gemini"):
            assert client.post("/casos/analisar", json=case).status_code == 502
        assert client.post("/reembolsos/executar", json=execute_for(case)).status_code == 403
        assert clients[0].closed
    logged = " | ".join(record.getMessage() for record in caplog.records)
    assert "ClientError 400 INVALID_ARGUMENT" in logged
    assert "credencial-ficticia-sem-rede" not in logged
    assert case["relato"] not in logged
    assert PROMPTS["evidencias"][:40] not in logged


def test_validation_error_log_never_contains_model_output(configuration, monkeypatch, caplog):
    leaked = "SEGREDO-NA-RESPOSTA-DO-MODELO"
    install_gemini_double(
        monkeypatch,
        configuration,
        transform=lambda schema, output: SimpleNamespace(parsed=None, text=f'{{"x": "{leaked}"}}'),
    )
    with TestClient(create_app(configuration)) as client:
        with caplog.at_level("WARNING", logger="app.agents.gemini"):
            assert client.post("/casos/analisar", json=case_for(client)).status_code == 502
    logged = " | ".join(record.getMessage() for record in caplog.records)
    assert "ValidationError" in logged
    assert leaked not in logged


def server_error(code=503, status="UNAVAILABLE"):
    return errors.ServerError(
        code, {"error": {"code": code, "status": status, "message": "high demand"}}
    )


def test_evidence_and_policy_agents_run_concurrently(configuration, monkeypatch):
    # Se as duas chamadas fossem sequenciais, a barreira estouraria o timeout e daria 502.
    barrier = threading.Barrier(2, timeout=5)

    def meet(schema, output):
        if schema in (ParecerEvidencias, RespostaPoliticas):
            barrier.wait()

    calls, clients = install_gemini_double(monkeypatch, configuration, transform=meet)
    with TestClient(create_app(configuration)) as client:
        response = client.post("/casos/analisar", json=case_for(client))
        assert response.status_code == 200, response.text
        assert len(calls) == 3
        assert clients[0].closed


@pytest.mark.parametrize("code,status", [(503, "UNAVAILABLE"), (504, "DEADLINE_EXCEEDED")])
def test_transient_provider_error_is_retried_once(configuration, monkeypatch, code, status):
    monkeypatch.setattr(gemini_module, "ESPERA_RETRY_SEGUNDOS", 0)
    failed = set()
    lock = threading.Lock()

    def flaky(schema, output):
        with lock:
            if schema not in failed:
                failed.add(schema)
                raise server_error(code, status)

    calls, _ = install_gemini_double(monkeypatch, configuration, transform=flaky)
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        response = client.post("/casos/analisar", json=case)
        assert_result(client, case, response, "APROVAR", True)
        assert len(calls) == 6


def test_persistent_overload_fails_closed_after_single_retry(configuration, monkeypatch):
    monkeypatch.setattr(gemini_module, "ESPERA_RETRY_SEGUNDOS", 0)

    def overloaded(schema, output):
        raise server_error()

    calls, clients = install_gemini_double(monkeypatch, configuration, transform=overloaded)
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        assert client.post("/casos/analisar", json=case).status_code == 502
        assert client.post("/reembolsos/executar", json=execute_for(case)).status_code == 403
        assert len(calls) == 4  # Evidências e Políticas: 1 tentativa + 1 retry cada.
        assert clients[0].closed


def test_non_transient_client_error_is_not_retried(configuration, monkeypatch):
    monkeypatch.setattr(gemini_module, "ESPERA_RETRY_SEGUNDOS", 0)

    def invalid(schema, output):
        raise errors.ClientError(
            400, {"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "schema"}}
        )

    calls, _ = install_gemini_double(monkeypatch, configuration, transform=invalid)
    with TestClient(create_app(configuration)) as client:
        assert client.post("/casos/analisar", json=case_for(client)).status_code == 502
        assert len(calls) == 2


def test_thinking_level_is_sent_only_when_configured(configuration, monkeypatch):
    calls, _ = install_gemini_double(monkeypatch, configuration)
    configuration.gemini_thinking_level = "low"
    with TestClient(create_app(configuration)) as client:
        assert client.post("/casos/analisar", json=case_for(client)).status_code == 200
    assert {call["config"].thinking_config.thinking_level for call in calls} == {
        types.ThinkingLevel.LOW
    }


def test_policy_citations_are_hydrated_from_retrieved_chunks(configuration, monkeypatch):
    calls, _ = install_gemini_double(monkeypatch, configuration)
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        response = client.post("/casos/analisar", json=case)
        result = assert_result(client, case, response, "APROVAR", True)
        policy_call = next(
            call
            for call in calls
            if call["config"].response_schema is SCHEMAS_LLM[RespostaPoliticas]
        )
        retrieved = {
            (item["documento"], item["versao"], item["secao"]): item
            for item in json.loads(policy_call["contents"])["trechos"]
        }
        assert result.parecer_politicas.citacoes_rejeitadas == []
        for citation in result.parecer_politicas.citacoes:
            assert citation.model_dump() == retrieved[citation.key]
            assert client.app.state.retriever.valid(citation)


def test_llm_policy_schema_never_asks_for_citation_text():
    fields = SCHEMAS_LLM[RespostaPoliticas].model_json_schema()["$defs"]["ReferenciaLLM"]
    assert set(fields["properties"]) == {"documento", "versao", "secao", "relevancia"}


def test_model_supplied_citation_text_is_rejected(configuration, monkeypatch):
    # O contrato estrito proíbe o modelo de enviar trecho próprio junto da referência.
    def smuggle(schema, output):
        if schema is RespostaPoliticas:
            data = output.model_dump(mode="json")
            data["citacoes"][0]["trecho"] = "Texto normativo forjado."
            return SimpleNamespace(parsed=None, text=json.dumps(data))

    install_gemini_double(monkeypatch, configuration, transform=smuggle)
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        assert client.post("/casos/analisar", json=case).status_code == 502
        assert client.post("/reembolsos/executar", json=execute_for(case)).status_code == 403


def test_hydration_rejects_unretrieved_sections_and_deduplicates():
    retrieved = [
        Citacao(documento="doc", versao="v1", secao="Seção 1", trecho="Texto 1", sha256="a"),
        Citacao(documento="doc", versao="v1", secao="Seção 2", trecho="Texto 2", sha256="b"),
    ]

    def ref(secao, versao="v1"):
        return ReferenciaCitacao(documento="doc", versao=versao, secao=secao, relevancia="x")

    parecer = hidratar_citacoes(
        RespostaPoliticas(
            dias_desde_entrega=2,
            elegivel_automatico=True,
            fundamentacao="ok",
            citacoes=[ref("Seção 2"), ref("Seção 2"), ref("Seção 1", versao="v2"), ref("Seção 9")],
        ),
        retrieved,
    )
    assert parecer.citacoes == [retrieved[1]]
    assert parecer.citacoes_rejeitadas == ["doc/v2/Seção 1", "doc/v1/Seção 9"]
    assert parecer.dias_desde_entrega == 2 and parecer.elegivel_automatico
