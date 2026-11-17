"""O n8n delega decisões ao Python e preserva a resposta na Gold."""

import json
import shutil
import subprocess

import pytest

from app.core.config import ROOT
from tests.test_pipeline import case_for


@pytest.mark.parametrize(
    "index,decision,authorized",
    [
        (1, "APROVAR", True),
        (2, "REJEITAR", False),
        (3, "REVISAO_HUMANA", False),
        (4, "APROVAR", True),
    ],
)
def test_exported_workflow_consumes_python_analysis(client, index, decision, authorized):
    case = case_for(client, f"PED-{index:03}")
    response = client.post("/casos/analisar", json=case)
    assert response.status_code == 200
    analysis = response.json()
    assert analysis["parecer_coordenador"]["recomendacao"] == decision
    assert analysis["autorizado_execucao"] is authorized
    final = {"status_execucao": "BLOQUEADO", "transaction_id": None, "erro": None}
    if authorized:
        body = {
            "case_id": case["case_id"],
            "pedido_id": case["pedido_id"],
            "valor": case["valor_solicitado"],
        }
        execution = client.post("/reembolsos/executar", json=body)
        if index == 4:
            assert execution.status_code == 503
            execution = client.post("/reembolsos/executar", json=body)
        assert execution.status_code == 200
        final = {
            "status_execucao": "CONCLUIDO",
            "transaction_id": execution.json()["transaction_id"],
            "erro": None,
        }
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js opcional: necess?rio somente para executar a montagem da Gold.")
    context = {
        "analise": analysis,
        "normalizado": {
            "caso": case,
            "bronze": [{"id": "bronze-teste"}],
            "config": {"equipe_email": "equipe@example.com"},
        },
        "silver": {"id": "silver-teste"},
        "final": final,
    }
    process = subprocess.run(
        [node, str(ROOT / "scripts/verify_workflow.cjs"), "--gold"],
        input=json.dumps(context),
        encoding="utf-8",
        capture_output=True,
        check=True,
        timeout=30,
    )
    gold = json.loads(process.stdout)
    assert gold["case_id"] == case["case_id"]
    assert gold["recomendacao"] == decision
    assert gold["autorizado_execucao"] is authorized
    assert gold["transaction_id"] == (final["transaction_id"] or "")
    assert gold["bronze_ids"] == "bronze-teste"
    assert json.loads(gold["resultado_json"])["analise"] == analysis


def test_workflow_has_one_analysis_http_node_and_no_agents():
    workflow = json.loads(
        (ROOT / "n8n/workflow_central_reembolsos.json").read_text(encoding="utf-8")
    )
    assert not any(node["type"].startswith("@n8n/") for node in workflow["nodes"])
    analysis = [node for node in workflow["nodes"] if node["name"] == "Analisar caso"]
    assert len(analysis) == 1
    assert "/casos/analisar" in analysis[0]["parameters"]["url"]
    execution = next(node for node in workflow["nodes"] if node["name"] == "Executar reembolso")
    assert execution["retryOnFail"] and execution["maxTries"] == 3
    assert execution["waitBetweenTries"] == 2000
