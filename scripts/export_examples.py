"""Exporta entradas para o webhook com as datas atuais do banco em execução."""

import argparse
import json
from pathlib import Path
from uuid import uuid4

import httpx

from app.core.config import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/examples")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with httpx.Client(base_url=args.api_url, timeout=30) as client:
        for index in range(1, 5):
            order_id = f"PED-{index:03}"
            response = client.get(f"/pedidos/{order_id}")
            response.raise_for_status()
            order = response.json()
            example = {
                "case_id": str(uuid4()),
                "pedido_id": order_id,
                "cliente_id": "CLI-DIVERGENTE" if index == 3 else order["cliente_id"],
                "cpf": order["cpf"],
                "email": "cliente@example.com",
                "valor_solicitado": "999.00" if index == 3 else order["valor_pago"],
                "motivo": "ARREPENDIMENTO",
                "relato": "Desejo desistir da compra online.",
                "data_entrega_informada": order["data_entrega"],
                "anexo_status": "ILEGIVEL" if index == 3 else "OK",
            }
            destination = args.output_dir / f"{order_id}.json"
            destination.write_text(
                json.dumps(example, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(destination)


if __name__ == "__main__":
    main()
