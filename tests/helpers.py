"""Payloads de integração gerados a partir dos pedidos e fontes reais da API."""

from uuid import uuid4

from app.core.schemas import CasoEntrada, Citacao, Pedido
from scripts.mock_agents import simulate_agents


def payload_for(client, pedido_id="PED-001", **overrides):
    order = Pedido.model_validate(client.get(f"/pedidos/{pedido_id}").json())
    values = {
        "case_id": uuid4(),
        "pedido_id": pedido_id,
        "cliente_id": order.cliente_id,
        "cpf": order.cpf,
        "email": "cliente@example.com",
        "valor_solicitado": order.valor_pago,
        "motivo": "ARREPENDIMENTO",
        "relato": "Desejo desistir da compra online.",
        "data_entrega_informada": order.data_entrega,
        "anexo_status": "OK",
    }
    values.update(overrides)
    result = client.post("/rag/consultar", json={"query": "arrependimento", "top_k": 20})
    result.raise_for_status()
    return simulate_agents(
        CasoEntrada(**values),
        order,
        [Citacao.model_validate(item) for item in result.json()["trechos"]],
        client.app.state.settings.limite_alcada_automatica,
    )


def payload_dict(client, pedido_id="PED-001", **overrides):
    return payload_for(client, pedido_id, **overrides).model_dump(mode="json")


def execution_payload(payload):
    case = payload["caso"]
    return {
        "pedido_id": case["pedido_id"],
        "case_id": case["case_id"],
        "valor": case["valor_solicitado"],
    }


def execution_for(payload):
    return execution_payload(payload.model_dump(mode="json"))
