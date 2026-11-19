"""Testa POST /casos/analisar nos quatro cenários, em API local ou banco temporário."""

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter, sleep
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.schemas import CasoEntrada, Pedido
from app.main import create_app


def run_scenarios(client: httpx.Client | TestClient, *, execute: bool) -> list[dict]:
    summaries = []
    for index in range(1, 5):
        response = client.get(f"/pedidos/PED-{index:03}")
        response.raise_for_status()
        order = Pedido.model_validate(response.json())
        case = CasoEntrada(
            case_id=uuid4(),
            pedido_id=order.pedido_id,
            cliente_id="CLI-DIVERGENTE" if index == 3 else order.cliente_id,
            cliente_nome=order.cliente_nome,
            cpf=order.cpf,
            email="cliente@example.com",
            valor_solicitado=Decimal("999.00") if index == 3 else order.valor_pago,
            motivo="ARREPENDIMENTO",
            relato="Solicito reembolso da compra online.",
            data_entrega_informada=order.data_entrega,
            anexo_status="ILEGIVEL" if index == 3 else "OK",
        )
        started = perf_counter()
        response = client.post("/casos/analisar", json=case.model_dump(mode="json"))
        elapsed_ms = round((perf_counter() - started) * 1000)
        if response.status_code != 200:
            # 502 = falha do agente (detalhe no log do Uvicorn); registra e segue para o próximo.
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:300]
            summaries.append(
                {
                    "pedido_id": case.pedido_id,
                    "erro": f"HTTP {response.status_code}: {detail}",
                    "tempo_cliente_ms": elapsed_ms,
                }
            )
            continue
        analysis = response.json()
        attempts, transaction = [], None
        if execute and analysis["autorizado_execucao"]:
            for _ in range(3):
                execution = client.post(
                    "/reembolsos/executar",
                    json={
                        "pedido_id": case.pedido_id,
                        "case_id": str(case.case_id),
                        "valor": str(case.valor_solicitado),
                    },
                )
                attempts.append(execution.status_code)
                if execution.status_code == 503:
                    sleep(2)
                    continue
                execution.raise_for_status()
                transaction = execution.json()["transaction_id"]
                break
            if transaction is None:
                raise RuntimeError("Execução sem confirmação após três tentativas.")
        summaries.append(
            {
                "pedido_id": case.pedido_id,
                "recomendacao": analysis["parecer_coordenador"]["recomendacao"],
                "autorizado_execucao": analysis["autorizado_execucao"],
                "motivo_bloqueio": analysis["motivo_bloqueio"],
                "modo": analysis["metadados"]["modo"],
                "modelo": analysis["metadados"]["modelo_usado"],
                "tempo_ms": analysis["metadados"]["tempo_total_ms"],
                "tempo_cliente_ms": elapsed_ms,
                "tentativas_http": attempts,
                "transaction_id": transaction,
            }
        )
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", help="API em execução; omitir usa banco temporário e mock.")
    parser.add_argument(
        "--executar",
        action="store_true",
        help="Executar tambem os reembolsos simulados na API informada.",
    )
    args = parser.parse_args()
    if args.api_url:
        # Alinhado ao nó "Analisar caso" do n8n (180 s): cobre timeout + retry dos agentes.
        with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=180.0) as client:
            summaries = run_scenarios(client, execute=args.executar)
    else:
        with TemporaryDirectory(prefix="reembolsos-demo-") as directory:
            settings = Settings(
                _env_file=None,
                use_mock_llm=True,
                use_mock_embeddings=True,
                google_api_key="",
                seed_demo_data=True,
                database_url=f"sqlite:///{Path(directory) / 'demo.db'}",
            )
            with TestClient(create_app(settings)) as client:
                summaries = run_scenarios(client, execute=True)
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    if any("erro" in summary for summary in summaries):
        sys.exit(1)


if __name__ == "__main__":
    main()
