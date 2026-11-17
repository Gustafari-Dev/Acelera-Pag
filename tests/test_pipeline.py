"""Novo endpoint com quatro cenários, parsing real do SDK e travas obrigatórias."""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google import genai
from google.genai import Client as OfficialGenaiClient
from google.genai import types
from pydantic import SecretStr

from app.agents.gemini import GeminiAgents
from app.agents.prompts import PROMPTS
from app.core.schemas import (
    CasoEntrada,
    Citacao,
    ParecerCoordenador,
    ParecerEvidencias,
    ParecerPoliticas,
    Pedido,
    Recomendacao,
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


def install_gemini_double(monkeypatch, configuration, representation="model", transform=None):
    calls, clients = [], []
    configuration.use_mock_llm = False
    configuration.google_api_key = SecretStr("credencial-ficticia-sem-rede")

    class ClientDouble:
        def __init__(self, **kwargs):
            self.options, self.models, self.closed = kwargs, self, False
            self.case, self.order = None, None
            clients.append(self)

        def generate_content(self, **kwargs):
            calls.append(kwargs)
            schema = kwargs["config"].response_schema
            context = json.loads(kwargs["contents"])
            if schema is ParecerEvidencias:
                self.case = CasoEntrada.model_validate(context["caso"])
                self.order = Pedido.model_validate(context["pedido_oficial"])
                output = analisar_evidencias(self.case, self.order)
            elif schema is ParecerPoliticas:
                output = analisar_politicas(
                    self.case,
                    self.order,
                    [Citacao.model_validate(item) for item in context["trechos"]],
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
        assert [call["config"].response_schema for call in calls] == [
            ParecerEvidencias,
            ParecerPoliticas,
            ParecerCoordenador,
        ]
        for call, prompt in zip(calls, PROMPTS.values(), strict=True):
            assert call["config"].system_instruction == prompt
            assert call["config"].response_mime_type == "application/json"
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
        assert len(calls) == 1
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


def test_wrong_citation_blocks_even_if_gemini_coordinator_approves(configuration, monkeypatch):
    def forge(schema, output):
        if schema is ParecerPoliticas:
            output.citacoes[0].trecho = "Norma criada pelo modelo."
        if schema is ParecerCoordenador:
            output.recomendacao = Recomendacao.APROVAR

    install_gemini_double(monkeypatch, configuration, transform=forge)
    with TestClient(create_app(configuration)) as client:
        case = case_for(client)
        response = client.post("/casos/analisar", json=case)
        assert response.status_code == 200
        assert not response.json()["autorizado_execucao"]
        assert "citacao_normativa_valida" in response.json()["motivo_bloqueio"]
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
    expected = [envelope.evidencias, envelope.politicas, envelope.coordenador]
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
            assert body["generationConfig"].get("responseSchema")
    finally:
        adapter.close()


@pytest.mark.parametrize("stage", [ParecerPoliticas, ParecerCoordenador])
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
