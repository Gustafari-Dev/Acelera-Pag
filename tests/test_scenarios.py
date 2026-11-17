"""Cenários ponta a ponta da API; os pareceres do n8n são simulados explicitamente."""

from uuid import UUID

from app.core.schemas import Recomendacao
from tests.helpers import execution_for, payload_for


def test_ped_001_valid_case(client):
    payload = payload_for(client)
    assert payload.evidencias.vinculo_valido
    assert not payload.evidencias.divergencias
    assert payload.politicas.elegivel_automatico
    assert payload.politicas.citacoes
    assert payload.coordenador.recomendacao == Recomendacao.APROVAR
    response = client.post("/validar", json=payload.model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["autorizado_execucao"] is True
    assert all(response.json()["validador"]["verificacoes"].values())
    execution = client.post("/reembolsos/executar", json=execution_for(payload))
    assert execution.status_code == 200
    assert UUID(execution.json()["transaction_id"])
    assert execution.json()["status"] == "CONCLUIDO"
    assert len(client.get("/pedidos/PED-001").json()["historico_estornos"]) == 1


def test_ped_002_duplicate_blocked(client):
    payload = payload_for(client, "PED-002")
    assert payload.evidencias.vinculo_valido
    assert payload.politicas.elegivel_automatico
    assert payload.coordenador.recomendacao == Recomendacao.REJEITAR
    response = client.post("/validar", json=payload.model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["autorizado_execucao"] is False
    assert "sem_reembolso_anterior" in response.json()["motivo_bloqueio"]
    assert client.post("/reembolsos/executar", json=execution_for(payload)).status_code == 409


def test_ped_003_divergence_requires_review(client):
    payload = payload_for(
        client,
        "PED-003",
        cliente_id="CLI-DIVERGENTE",
        valor_solicitado="999.00",
        anexo_status="ILEGIVEL",
    )
    assert payload.evidencias.divergencias
    assert payload.evidencias.informacoes_ausentes
    assert not payload.evidencias.vinculo_valido
    assert payload.politicas.citacoes
    assert payload.coordenador.recomendacao == Recomendacao.REVISAO_HUMANA
    response = client.post("/validar", json=payload.model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["autorizado_execucao"] is False
    assert "vinculo_cliente_valido" in response.json()["motivo_bloqueio"]
    assert client.post("/reembolsos/executar", json=execution_for(payload)).status_code == 403


def test_ped_004_recovers_after_temporary_failure(client):
    payload = payload_for(client, "PED-004")
    assert payload.evidencias.vinculo_valido
    assert payload.politicas.elegivel_automatico
    assert payload.coordenador.recomendacao == Recomendacao.APROVAR
    validation = client.post("/validar", json=payload.model_dump(mode="json"))
    assert validation.json()["autorizado_execucao"] is True
    first = client.post("/reembolsos/executar", json=execution_for(payload))
    assert first.status_code == 503
    assert first.headers["Retry-After"] == "2"
    assert not client.get("/pedidos/PED-004").json()["historico_estornos"]
    second = client.post("/reembolsos/executar", json=execution_for(payload))
    assert second.status_code == 200
    repeated = client.post("/reembolsos/executar", json=execution_for(payload))
    assert repeated.json()["transaction_id"] == second.json()["transaction_id"]
    assert len(client.get("/pedidos/PED-004").json()["historico_estornos"]) == 1
