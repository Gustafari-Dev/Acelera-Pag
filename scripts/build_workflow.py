"""Gera o workflow importável e contratos a partir dos mesmos schemas do backend."""

import copy
import json
from uuid import NAMESPACE_URL, uuid5

from app.agents.prompts import PROMPTS, VERSAO_PROMPTS
from app.core.config import ROOT
from app.core.schemas import (
    CasoEntrada,
    ParecerCoordenador,
    ParecerEvidencias,
    ParecerPoliticas,
    ResultadoAnalise,
    ValidacaoEntrada,
)

N8N = ROOT / "n8n"


def inline_schema(schema: dict) -> dict:
    definitions = schema.get("$defs", {})

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return expand(copy.deepcopy(definitions[value["$ref"].split("/")[-1]]))
        return {key: expand(item) for key, item in value.items() if key not in {"$defs", "title"}}

    return expand(schema)


def build() -> dict:
    prompts = PROMPTS
    classes = {
        "evidencias": ParecerEvidencias,
        "politicas": ParecerPoliticas,
        "coordenador": ParecerCoordenador,
    }
    schemas = {name: inline_schema(model.model_json_schema()) for name, model in classes.items()}
    schema_dir = N8N / "schemas"
    schema_dir.mkdir(exist_ok=True)
    for name, schema in {
        **schemas,
        "caso_entrada": CasoEntrada.model_json_schema(),
        "resultado_analise": ResultadoAnalise.model_json_schema(),
        "validacao_entrada": ValidacaoEntrada.model_json_schema(),
    }.items():
        (schema_dir / f"{name}.json").write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    prompt_doc = [
        "# Agentes Python: arquitetura, prompts e contratos\n",
        f"Prompts {VERSAO_PROMPTS}, gerados diretamente de `app/agents/prompts.py`. "
        "Edite essa fonte e rode `python -m scripts.build_workflow`.\n",
        "O pipeline Python executa Evidências -> Políticas -> Coordenador -> "
        "Validador determinístico. Usa `google-genai`, `generate_content` e "
        "`response_schema` Pydantic. Sem chave ou com USE_MOCK_LLM=true, usa "
        "o fallback de `scripts/mock_agents.py`.\n",
        "Relatos e fontes são dados não confiáveis. As citações são copiadas "
        "integralmente e conferidas pelo validador. O n8n apenas chama "
        "POST /casos/analisar e encaminha o resultado.\n",
        "Cada resposta inclui hashes dos prompts, entradas e saídas das etapas, "
        "modelo, modo, datas e tempos de execução. Respostas inválidas ou falhas "
        "do Gemini retornam 502, sem autorização e sem fallback silencioso.\n",
    ]
    for name in classes:
        prompt_doc.extend(
            [
                f"## {name.capitalize()}\n",
                f"### System prompt\n\n{prompts[name]}\n",
                "### Schema JSON de saída\n\n```json\n"
                + json.dumps(schemas[name], ensure_ascii=False, indent=2)
                + "\n```\n",
            ]
        )
    (ROOT / "docs/arquitetura_agentes.md").write_text("\n".join(prompt_doc), encoding="utf-8")
    (ROOT / "docs/prompts_agentes_n8n.md").write_text(
        "# Prompts dos agentes\n\nOs agentes agora executam em Python. "
        "Consulte [arquitetura_agentes.md](arquitetura_agentes.md).\n",
        encoding="utf-8",
    )

    nodes, connections = [], {}

    def add(name: str, kind: str, parameters: dict, x: int, y: int = 0, version=1, **extra):
        node = {
            "id": str(uuid5(NAMESPACE_URL, f"central-reembolsos/{name}")),
            "name": name,
            "type": kind,
            "typeVersion": version,
            "position": [x, y],
            "parameters": parameters,
            **extra,
        }
        nodes.append(node)
        return name

    def connect(source: str, target: str, output=0, kind="main"):
        outputs = connections.setdefault(source, {}).setdefault(kind, [])
        while len(outputs) <= output:
            outputs.append([])
        outputs[output].append({"node": target, "type": kind, "index": 0})

    def code(name: str, source: str, x: int, y=0, **extra):
        return add(name, "n8n-nodes-base.code", {"jsCode": source}, x, y, version=2, **extra)

    def source(filename: str) -> str:
        return (N8N / "code" / filename).read_text(encoding="utf-8")

    def http(name: str, method: str, path: str, x: int, body=None, timeout=60000, **extra):
        params = {
            "method": method,
            "url": "={{ $('Gerar caso').first().json.config.api_url + " + path + " }}",
            "options": {"timeout": timeout, "response": {"response": {"responseFormat": "json"}}},
        }
        if body:
            params.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
        return add(name, "n8n-nodes-base.httpRequest", params, x, version=4.2, **extra)

    def drive(name: str, folder: str, filename: str, x: int):
        return add(
            name,
            "n8n-nodes-base.googleDrive",
            {
                "resource": "file",
                "operation": "upload",
                "inputDataFieldName": "data",
                "name": filename,
                "driveId": {"__rl": True, "mode": "list", "value": "My Drive"},
                "folderId": {
                    "__rl": True,
                    "mode": "id",
                    "value": "={{ $('Gerar caso').first().json.config." + folder + " }}",
                },
                "options": {},
            },
            x,
            version=3,
        )

    def condition(name: str, expression: str, x: int):
        return add(
            name,
            "n8n-nodes-base.if",
            {
                "conditions": {
                    "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                    "conditions": [
                        {
                            "id": str(uuid5(NAMESPACE_URL, name)),
                            "leftValue": expression,
                            "rightValue": True,
                            "operator": {
                                "type": "boolean",
                                "operation": "true",
                                "singleValue": True,
                            },
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
            x,
            version=2.2,
        )

    add(
        "Entrada",
        "n8n-nodes-base.webhook",
        {
            "httpMethod": "POST",
            "path": "central-reembolsos",
            "responseMode": "responseNode",
            "options": {},
        },
        0,
        version=2,
        webhookId=str(uuid5(NAMESPACE_URL, "central-reembolsos-webhook")),
    )
    code("Gerar caso", source("gerar_caso.js"), 220)
    code("Preparar Bronze", source("preparar_bronze.js"), 440)
    drive("Salvar Bronze", "bronze_folder", "={{ $json.nome_arquivo }}", 660)
    code("Normalizar", source("normalizar.js"), 880)
    drive("Salvar Silver", "silver_folder", "={{ $binary.data.fileName }}", 1100)
    http(
        "Analisar caso",
        "POST",
        "'/casos/analisar'",
        1320,
        "={{ $('Normalizar').first().json.caso }}",
        timeout=180000,
    )
    condition("Autorizado?", "={{ $json.autorizado_execucao === true }}", 1540)
    http(
        "Executar reembolso",
        "POST",
        "'/reembolsos/executar'",
        1760,
        "={{ {pedido_id: $('Analisar caso').first().json.caso.pedido_id, "
        "case_id: $('Analisar caso').first().json.caso.case_id, "
        "valor: $('Analisar caso').first().json.caso.valor_solicitado} }}",
        retryOnFail=True,
        maxTries=3,
        waitBetweenTries=2000,
        onError="continueErrorOutput",
    )
    http(
        "Verificar historico",
        "GET",
        "'/pedidos/' + $('Analisar caso').first().json.caso.pedido_id",
        1980,
        onError="continueErrorOutput",
    )
    code(
        "Consolidar executado",
        source("consolidar_executado.js"),
        2200,
        onError="continueErrorOutput",
    )
    code(
        "Consolidar bloqueado",
        "return [{json:{status_execucao:'BLOQUEADO',transaction_id:null,erro:null}}];",
        1980,
        -220,
    )
    code(
        "Consolidar falha",
        "return [{json:{status_execucao:'NAO_CONFIRMADO',transaction_id:null,"
        "erro:JSON.stringify($input.first().json)}}];",
        2200,
        220,
    )
    code("Montar Gold", source("montar_gold.js"), 2420)
    columns = [
        "case_id",
        "pedido_id",
        "email",
        "valor",
        "recomendacao",
        "autorizado_execucao",
        "status_execucao",
        "transaction_id",
        "motivo_bloqueio",
        "recebido_em",
        "bronze_ids",
        "silver_id",
        "citacoes_json",
        "evidencias_json",
        "resultado_json",
    ]
    add(
        "Salvar Gold",
        "n8n-nodes-base.googleSheets",
        {
            "operation": "appendOrUpdate",
            "documentId": {
                "__rl": True,
                "mode": "id",
                "value": "={{ $('Gerar caso').first().json.config.gold_sheet }}",
            },
            "sheetName": {
                "__rl": True,
                "mode": "name",
                "value": "={{ $('Gerar caso').first().json.config.gold_tab }}",
            },
            "columns": {
                "mappingMode": "defineBelow",
                "value": {field: "={{ $json." + field + " }}" for field in columns},
                "matchingColumns": ["case_id"],
                "schema": [
                    {
                        "id": field,
                        "displayName": field,
                        "required": False,
                        "defaultMatch": field == "case_id",
                        "display": True,
                        "type": "string",
                        "canBeUsedToMatch": True,
                    }
                    for field in columns
                ],
                "attemptToConvertTypes": False,
                "convertFieldsToString": True,
            },
            "options": {"cellFormat": "RAW"},
        },
        2640,
        version=4.5,
    )
    add(
        "Enviar resposta",
        "n8n-nodes-base.gmail",
        {
            "resource": "message",
            "operation": "send",
            "emailType": "text",
            "sendTo": "={{ $('Montar Gold').first().json.destinatario }}",
            "subject": "={{ 'Central de Reembolsos - ' + $('Montar Gold').first().json.case_id }}",
            "message": "={{ $('Montar Gold').first().json.mensagem_email }}",
            "options": {"appendAttribution": False},
        },
        2860,
        version=2.1,
    )
    add(
        "Responder webhook",
        "n8n-nodes-base.respondToWebhook",
        {
            "respondWith": "json",
            "responseBody": "={{ $('Montar Gold').first().json }}",
            "options": {},
        },
        3080,
        version=1.4,
    )

    linear = [
        "Entrada",
        "Gerar caso",
        "Preparar Bronze",
        "Salvar Bronze",
        "Normalizar",
        "Salvar Silver",
        "Analisar caso",
        "Autorizado?",
    ]
    for a, b in zip(linear, linear[1:], strict=False):
        connect(a, b)
    for a, b in [
        ("Executar reembolso", "Verificar historico"),
        ("Verificar historico", "Consolidar executado"),
        ("Consolidar executado", "Montar Gold"),
        ("Consolidar bloqueado", "Montar Gold"),
        ("Consolidar falha", "Montar Gold"),
        ("Montar Gold", "Salvar Gold"),
        ("Salvar Gold", "Enviar resposta"),
        ("Enviar resposta", "Responder webhook"),
    ]:
        connect(a, b)
    connect("Autorizado?", "Executar reembolso", output=0)
    connect("Autorizado?", "Consolidar bloqueado", output=1)
    for node in ["Executar reembolso", "Verificar historico", "Consolidar executado"]:
        connect(node, "Consolidar falha", output=1)
    return {
        "name": "Central de Reembolsos - agentes Python",
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1", "timezone": "America/Sao_Paulo"},
        "pinData": {},
        "meta": {"templateCredsSetupCompleted": False},
        "versionId": str(uuid5(NAMESPACE_URL, "central-reembolsos-v1")),
        "tags": [],
    }


def main() -> None:
    workflow = build()
    path = N8N / "workflow_central_reembolsos.json"
    path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Workflow gerado: {len(workflow['nodes'])} nós em {path}")


if __name__ == "__main__":
    main()
