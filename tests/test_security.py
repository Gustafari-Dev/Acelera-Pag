"""Travas reais são exercitadas inclusive quando os pareceres simulados mentem."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.schemas import CasoEntrada
from app.main import create_app
from tests.helpers import execution_payload
from tests.helpers import payload_dict as payload_for


def test_no_execution_without_validation(client):
    assert (
        client.post(
            "/reembolsos/executar",
            json={"pedido_id": "PED-001", "case_id": str(uuid4()), "valor": "199.90"},
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("secao", "Seção 99.9"),
        ("versao", "v999"),
        ("trecho", "Regra inventada pelo LLM"),
        ("sha256", "0" * 64),
    ],
)
def test_fabricated_citation_is_blocked(client, field, value):
    payload = payload_for(client)
    payload["politicas"]["citacoes"][0][field] = value
    payload["coordenador"]["recomendacao"] = "APROVAR"
    result = client.post("/validar", json=payload).json()
    assert result["autorizado_execucao"] is False
    assert "citacao_normativa_valida" in result["motivo_bloqueio"]


def test_missing_citation_is_blocked(client):
    payload = payload_for(client)
    payload["politicas"]["citacoes"] = []
    assert not client.post("/validar", json=payload).json()["autorizado_execucao"]


def test_official_price_cannot_be_overridden(client):
    payload = payload_for(client)
    payload["caso"]["valor_solicitado"] = "299.90"
    payload["pedido_informado"]["valor_pago"] = "9999.99"
    result = client.post("/validar", json=payload).json()
    assert not result["autorizado_execucao"]
    assert not result["validador"]["verificacoes"]["valor_nao_supera_pago"]
    assert not result["validador"]["verificacoes"]["pedido_informado_confere"]


@pytest.mark.parametrize("field", ["cpf", "cliente_id"])
def test_one_correct_identifier_cannot_mask_other_mismatch(client, field):
    payload = payload_for(client)
    payload["caso"][field] = "99999999999" if field == "cpf" else "CLI-ERRADO"
    assert not client.post("/validar", json=payload).json()["autorizado_execucao"]


def test_amount_binding_and_case_binding(client):
    payload = payload_for(client)
    assert client.post("/validar", json=payload).json()["autorizado_execucao"]
    execution = execution_payload(payload)
    execution["valor"] = "1.00"
    assert client.post("/reembolsos/executar", json=execution).status_code == 409
    payload["caso"]["relato"] = "Outro conteúdo para o mesmo UUID"
    assert client.post("/validar", json=payload).status_code == 409


def test_alcada_is_enforced_even_if_coordinator_approves(client):
    payload = payload_for(client)
    client.app.state.settings.limite_alcada_automatica = Decimal("100.00")
    result = client.post("/validar", json=payload).json()
    assert not result["autorizado_execucao"]
    assert "dentro_da_alcada" in result["motivo_bloqueio"]


def test_changed_order_is_rechecked_at_execution(client):
    payload = payload_for(client)
    client.post("/validar", json=payload).raise_for_status()
    with client.app.state.database.connection(immediate=True) as connection:
        connection.execute("UPDATE pedidos SET cliente_id = 'OUTRO' WHERE pedido_id = 'PED-001'")
    assert client.post("/reembolsos/executar", json=execution_payload(payload)).status_code == 403


def test_expired_authorization(client, monkeypatch):
    payload = payload_for(client)
    client.post("/validar", json=payload).raise_for_status()
    now = client.app.state.settings.now()
    monkeypatch.setattr(Settings, "now", lambda self: now + timedelta(hours=1))
    assert client.post("/reembolsos/executar", json=execution_payload(payload)).status_code == 403


def test_concurrent_execution_creates_one_transaction(client):
    first, second = payload_for(client), payload_for(client)
    for payload in (first, second):
        assert client.post("/validar", json=payload).json()["autorizado_execucao"]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda payload: client.post(
                    "/reembolsos/executar", json=execution_payload(payload)
                ),
                (first, second),
            )
        )
    assert sorted(item.status_code for item in results) == [200, 409]
    assert len(client.get("/pedidos/PED-001").json()["historico_estornos"]) == 1


def test_restart_preserves_failure_counter_and_idempotency(client, configuration):
    payload = payload_for(client, "PED-004")
    client.post("/validar", json=payload).raise_for_status()
    assert client.post("/reembolsos/executar", json=execution_payload(payload)).status_code == 503
    with TestClient(create_app(configuration)) as restarted:
        response = restarted.post("/reembolsos/executar", json=execution_payload(payload))
        assert response.status_code == 200
        again = restarted.post("/reembolsos/executar", json=execution_payload(payload))
        assert response.json() == again.json()


@pytest.mark.parametrize("offset,authorized", [(0, True), (7, True), (8, False), (-1, False)])
def test_withdrawal_window_cannot_be_overridden(client, offset, authorized):
    today = client.app.state.settings.now().date()
    with client.app.state.database.connection(immediate=True) as connection:
        connection.execute(
            "UPDATE pedidos SET data_entrega = ? WHERE pedido_id = 'PED-001'",
            ((today - timedelta(days=offset)).isoformat(),),
        )
    payload = payload_for(client)
    payload["coordenador"]["recomendacao"] = "APROVAR"
    payload["politicas"]["elegivel_automatico"] = True
    result = client.post("/validar", json=payload).json()
    assert result["autorizado_execucao"] is authorized


@pytest.mark.parametrize("amount", ["0", "-1.00", "NaN", "Infinity", "1.001", True])
def test_invalid_money_is_rejected(client, amount):
    payload = payload_for(client)
    payload["caso"]["valor_solicitado"] = amount
    assert client.post("/validar", json=payload).status_code == 422


def test_unknown_fields_and_string_boolean_are_rejected(client):
    payload = payload_for(client)
    payload["autorizado_execucao"] = True
    assert client.post("/validar", json=payload).status_code == 422
    del payload["autorizado_execucao"]
    payload["politicas"]["elegivel_automatico"] = "true"
    assert client.post("/validar", json=payload).status_code == 422


def test_missing_key_enables_offline_fallback():
    settings = Settings(
        _env_file=None, use_mock_llm=False, use_mock_embeddings=False, google_api_key=""
    )
    assert settings.mock_llm
    assert settings.mock_embeddings


def test_cpf_format_is_normalized(client):
    payload = payload_for(client)
    payload["caso"]["cpf"] = "111.111.111-11"
    assert CasoEntrada.model_validate(payload["caso"]).cpf == "11111111111"


def test_rag_preserves_exact_metadata_and_does_not_mix_sections(client):
    retriever = client.app.state.retriever
    for citation in retriever.search("arrependimento", k=20):
        assert retriever.valid(citation)
        assert citation.trecho.startswith(f"## {citation.secao} -")
        assert citation.trecho.count("## Seção") == 1
    assert client.post("/rag/consultar", json={"query": "", "top_k": 0}).status_code == 422


def test_validar_externo_disabled_by_default_and_direct_execution_is_forbidden(configuration):
    from uuid import uuid4

    from fastapi.testclient import TestClient

    from app.main import create_app

    strict = configuration.model_copy(update={"permitir_validar_externo": False})
    with TestClient(create_app(strict)) as client:
        from tests.helpers import payload_dict

        assert client.post("/validar", json=payload_dict(client)).status_code == 403
        direct = {"pedido_id": "PED-001", "case_id": str(uuid4()), "valor": "199.90"}
        assert client.post("/reembolsos/executar", json=direct).status_code == 403
        assert not client.get("/pedidos/PED-001").json()["historico_estornos"]
