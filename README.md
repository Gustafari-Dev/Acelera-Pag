# Central de Reembolsos

Protótipo local para analisar reembolsos de e-commerce, com pedidos e transações
simulados. O **FastAPI executa os três agentes em Python puro**, usando o SDK
oficial `google-genai`, o RAG rastreável e o validador determinístico.
O **n8n organiza webhooks, Bronze, Silver, Gold e comunicação**, consumindo
`POST /casos/analisar`. O projeto não usa LangChain nem LangGraph.

## Estrutura

```text
.
├── app/
│   ├── api/routes.py            # Contratos HTTP para o n8n
│   ├── agents/
│   │   ├── prompts.py          # Fonte única dos três System Prompts
│   │   ├── gemini.py           # generate_content + response_schema Pydantic
│   │   └── pipeline.py         # Evidências -> Políticas -> Coordenador -> Validador
│   ├── core/
│   │   ├── config.py           # .env, limites, relógio e flags mock
│   │   ├── errors.py
│   │   ├── schemas.py          # Pydantic; campos extras proibidos
│   │   └── validator.py        # Travas financeiras sem LLM
│   ├── db/database.py          # SQLite, seed e idempotência
│   ├── rag/
│   │   ├── embeddings.py       # Gemini ou hashing local
│   │   ├── ingest.py           # Uma seção numerada por vetor
│   │   └── retriever.py        # Trechos com metadados e hash
│   └── main.py
├── data/
│   ├── examples/               # Entradas geradas para o webhook
│   ├── reembolsos.db           # Criado na primeira inicialização
│   └── chroma/                 # Índice persistido automaticamente
├── docs/
│   ├── arquitetura.md
│   ├── arquitetura_agentes.md  # Prompts reais e schemas, gerados da fonte Python
│   ├── cdc_artigos_selecionados_v1.md
│   ├── politica_interna_reembolsos_v1.md
│   ├── prompts_agentes_n8n.md
│   └── validacao_local.md
├── n8n/
│   ├── code/                  # Código dos nós Code, legível e editável
│   ├── schemas/               # Contratos JSON dos agentes e do validador
│   ├── README.md              # Configuração nó a nó e credenciais
│   └── workflow_central_reembolsos.json
├── scripts/
│   ├── build_workflow.py       # Regenera JSON, schemas e documentação de prompts
│   ├── demo.py                 # Quatro cenários no endpoint, local ou API em execução
│   ├── export_examples.py      # Entradas com datas do banco em execução
│   ├── mock_agents.py          # Fallback determinístico dos agentes Python
│   └── verify_workflow.cjs     # Sintaxe/conexões e montagem da Gold, sem serviços externos
├── tests/
├── .env.example
├── requirements.txt
├── requirements.lock.txt
└── pyproject.toml
```

## Instalação no Windows / PowerShell

Use Python **3.11 ou superior**; a validação deste workspace foi feita com
Python **3.12**. Instale Python com o launcher `py` e confirme:

```powershell
py -0p
py -3.12 --version
Set-Location 'C:\Users\gusta\OneDrive\Desktop\Acelera Pag'
py -3.12 -m venv .venv
```

Se preferir Python 3.11, substitua `-3.12` por `-3.11`. Este workspace já inclui
uma `.venv` preparada; nesse caso, pode começar pelos comandos abaixo. Não é
necessário ativá-la: chamar o executável explicitamente evita bloqueios de scripts
do PowerShell.

```powershell
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item -LiteralPath .env.example -Destination .env
notepad .env
```

Para repetir as versões verificadas em Windows com Python 3.12, use
`-r requirements.lock.txt` no lugar de `-r requirements.txt`. O lock foi resolvido
para esse ambiente; para Python 3.11, use as faixas de `requirements.txt`.
O download das dependências requer internet uma vez. Depois da instalação,
ingestão, API e testes mock funcionam sem internet.

Ativação opcional:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

A alteração de política acima vale somente para a janela atual. Se ela for
bloqueada pela organização, continue usando `.\.venv\Scripts\python.exe`.

## Primeira execução sem chave do Google

Deixe estas linhas no `.env`:

```dotenv
USE_MOCK_LLM=true
USE_MOCK_EMBEDDINGS=true
GOOGLE_API_KEY=
DATABASE_URL=sqlite:///./data/reembolsos.db
CHROMA_PERSIST_DIR=./data/chroma
LIMITE_ALCADA_AUTOMATICA=1000.00
```

Execute os comandos a partir da raiz do projeto:

```powershell
.\.venv\Scripts\python.exe -m app.rag.ingest
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m scripts.demo
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

A ingestão é idempotente e também ocorre automaticamente na inicialização da API.
São sete seções, cada uma com `documento`, `versao`, `secao`, texto original e
SHA-256. Não há download de modelos no modo mock. Seus embeddings são vetores
lexicais por hashing, destinados aos testes, com qualidade diferente dos vetores Gemini.

Acesse `http://127.0.0.1:8000/docs` para o Swagger e
`http://127.0.0.1:8000/redoc` para os contratos. Em outra janela:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/pedidos/PED-001
$consulta = @{ query = 'arrependimento em ate sete dias'; top_k = 5 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/rag/consultar `
  -ContentType 'application/json; charset=utf-8' -Body $consulta
```

`scripts.demo` chama o novo endpoint `/casos/analisar` via TestClient, com banco
temporário e agentes mock. Também exercita a execução e a recuperação do 503.
Ele não depende do n8n, não envia e-mails e não consome os pedidos da sua API
em execução. Os testes não precisam de `.env`: usam bancos isolados e clientes
Gemini simulados, sem consumir cota.

## Testar os quatro casos na API em execução

Com Uvicorn iniciado, um único comando busca os pedidos oficiais, monta quatro
entradas Silver com UUIDs novos e chama `POST /casos/analisar` para cada uma:

```powershell
.\.venv\Scripts\python.exe -m scripts.demo --api-url http://127.0.0.1:8000
```

O comando retorna recomendação, autorização/bloqueio, modelo e tempo de cada
caso. Sem `--executar` ele não cria reembolsos nem consome o contador do PED-004,
mas **grava** uma análise por caso (tabelas `casos`/`analises`) no banco da API e, com
chave Gemini real, consome cota (3 chamadas por caso). Acrescente `--executar` para testar também os estornos
simulados e o retry do `PED-004`; isso altera o banco da API informada.
Sem `--api-url`, o comando usa banco temporário e executa os quatro cenários
completos em modo mock, sem precisar iniciar o servidor.

## Alternar mock e Gemini no backend

Para operação inteiramente offline após instalar as dependências:

```dotenv
USE_MOCK_LLM=true
USE_MOCK_EMBEDDINGS=true
GOOGLE_API_KEY=
```

Para os três agentes reais, mantendo a busca local com embeddings mock:

```dotenv
USE_MOCK_LLM=false
USE_MOCK_EMBEDDINGS=true
GOOGLE_API_KEY=sua_chave_real
GEMINI_MODEL=gemini-3.5-flash
```

Para embeddings Gemini também, altere `USE_MOCK_EMBEDDINGS=false` e execute a
ingestão novamente. Reinicie o Uvicorn depois de alterar o `.env` e use um novo
`case_id` para uma nova análise; UUIDs já analisados retornam o resultado salvo.
Sem `GOOGLE_API_KEY`, agentes e embeddings usam mock automaticamente. Com chave
e agentes reais ativos, erros de rede/quota ou JSON inválido retornam `502`, sem
autorização e sem fallback silencioso para uma aprovação mock.

Cada chamada real usa `client.models.generate_content` com `response_schema`
Pydantic e `response_mime_type=application/json`. Os modelos de saída são
revalidados localmente. Os prompts usados estão em `app/agents/prompts.py` e
documentados em [docs/arquitetura_agentes.md](docs/arquitetura_agentes.md).

## Endpoints

| Método e caminho | Comportamento |
| --- | --- |
| `GET /health` | Modo de embeddings, data de referência e alçada vigente |
| `GET /pedidos/{pedido_id}` | Cliente, itens, valor, entrega e histórico oficial |
| `POST /casos/analisar` | Entrada Silver `CasoEntrada`; três agentes e validador Python |
| `POST /rag/consultar` | Recebe `query`, `top_k`; retorna `trechos` e `modo` |
| `POST /validar` | Legado, **desativado por padrão** (403): aceita pareceres do cliente. Habilite só para testes com `PERMITIR_VALIDAR_EXTERNO=true` |
| `POST /reembolsos/executar` | Exige autorização persistida; recebe pedido, UUID e valor |

O novo endpoint recebe diretamente `CasoEntrada`, sem pareceres fornecidos pelo
chamador. `cliente_nome` é opcional; quando informado, é comparado com o titular
oficial. Nome sozinho não estabelece identidade: CPF/ID compatível continua
obrigatório. O retorno `ResultadoAnalise` contém:

```text
caso, pedido_oficial, parecer_evidencias, parecer_politicas, parecer_coordenador,
validacao, autorizado_execucao, motivo_bloqueio, metadados
```

Os metadados incluem modo efetivo, motivo do mock, modelo, tempo total, horários,
UUID da análise e hashes dos prompts e das entradas/saídas das seis etapas.
O pipeline salva a análise e a autorização numa única transação SQLite; somente
depois responde ao n8n. O n8n usa `autorizado_execucao === true` para rotear a
execução. O endpoint não executa estornos. Os endpoints anteriores mantêm os seus
contratos. Schemas completos: `n8n/schemas/caso_entrada.json` e
`n8n/schemas/resultado_analise.json`.

O corpo de `/validar` contém `caso`, `evidencias`, `politicas`, `coordenador` e,
opcionalmente, `pedido_informado`. O backend lê o pedido oficial do SQLite;
o snapshot enviado pelo n8n serve apenas para detectar divergência.
O contrato completo está em [n8n/schemas/validacao_entrada.json](n8n/schemas/validacao_entrada.json).
Valores monetários devem ser enviados preferencialmente como strings decimais,
por exemplo `"199.90"`. Valores não positivos ou com mais de duas casas são recusados.

O retorno de `/validar` inclui `autorizado_execucao`, `motivo_bloqueio` e todas
as `verificacoes` dentro de `validador`. O coordenador recomendar `APROVAR` não
substitui as verificações. Citações devem copiar o objeto recuperado integralmente,
inclusive `trecho` e `sha256`. Citação inventada ou modificada bloqueia a execução.

Uma execução autorizada responde, por exemplo:

```json
{
  "transaction_id": "UUID gerado pelo servidor",
  "case_id": "UUID validado anteriormente",
  "pedido_id": "PED-001",
  "valor": "199.90",
  "status": "CONCLUIDO",
  "criado_em": "data e hora da operação"
}
```

Esse exemplo descreve os campos; UUIDs e datas reais são retornados pela API.
Repetir a mesma execução retorna o mesmo recibo. Usar o UUID com outro pedido,
valor ou conteúdo de validação resulta em `409`. Corrigir uma solicitação exige
um novo `case_id`. Autorizações expiram após 900 segundos por padrão. O recibo de
uma transação já concluída continua disponível nas retentativas da mesma operação.

`403` indica ausência/expiração de autorização ou bloqueio; `404`, pedido ausente;
`409`, duplicidade ou conflito; `422`, contrato inválido; `502`, falha no serviço
de embeddings; `503`, falha temporária simulada com `Retry-After: 2`.

## Pedidos e resultados esperados

| Pedido | Dados de demonstração | Resultado esperado |
| --- | --- | --- |
| `PED-001` | R$ 199,90; entregue há 3 dias | Aprovação e execução |
| `PED-002` | R$ 150,00; já possui estorno | Bloqueio por duplicidade |
| `PED-003` | Oficial R$ 250,00; exemplo relata R$ 999,00 e anexo ilegível | Revisão humana |
| `PED-004` | R$ 89,90; entregue há 1 dia | Primeiro HTTP 503; segundo HTTP 200 |

Os CPFs e nomes são fictícios. A conferência usa correspondência com o cadastro
simulado; não há consulta cadastral nem validação de autenticidade documental.
O cenário 3 depende do relato divergente, não de adulterar o pedido oficial.

As datas são calculadas na inicialização: pedidos de demonstração ainda intocados
(sem reembolso e sem tentativa de execução) têm a entrega renovada, mantendo a
janela de 7 dias. Reiniciar preserva transações e contadores. Para repetir a
demonstração do zero, pare a API e altere `DATABASE_URL` para um novo arquivo,
por exemplo `sqlite:///./data/demo_sessao_2.db`, e inicie novamente. O seed não
apaga operações anteriores. `SEED_DEMO_DATA=false` desliga os dados de demonstração.

## n8n e Google Workspace

Importe [n8n/workflow_central_reembolsos.json](n8n/workflow_central_reembolsos.json)
usando **Import from File** no editor do n8n. O workflow é entregue desativado.
Siga [n8n/README.md](n8n/README.md) para configurar variáveis, credenciais OAuth,
pastas e cabeçalhos da planilha. O workflow tem 17 nós e uma única chamada para
análise: não contém nós LLM nem cópias dos agentes ou das políticas.

Para n8n em Docker Desktop, use `CENTRAL_API_URL=http://host.docker.internal:8000`
e inicie a API com `--host 0.0.0.0` para acesso pelo contêiner em ambiente local.
Para n8n nativo no Windows, use `http://127.0.0.1:8000`. O `.env` do backend não
é automaticamente carregado pelo processo do n8n.

Com a API em execução, gere quatro entradas atualizadas:

```powershell
.\.venv\Scripts\python.exe -m scripts.export_examples
$payload = Get-Content -LiteralPath data/examples/PED-001.json -Raw -Encoding UTF8
Invoke-RestMethod -Method Post -Uri http://localhost:5678/webhook-test/central-reembolsos `
  -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload))
```

Antes da chamada, clique **Listen for test event** no Webhook do n8n. Para o
workflow ativado, use `/webhook/central-reembolsos`. A integração completa requer
Drive, Sheets e Gmail conectados, mesmo com agentes mock. A API e a suíte local
continuam independentes desses serviços. Anexos binários recebidos no webhook
são preservados na Bronze; extração por OCR não faz parte deste protótipo.

## Embeddings reais

No `.env`, preencha `GOOGLE_API_KEY`, defina `USE_MOCK_EMBEDDINGS=false` e mantenha
`GEMINI_EMBEDDING_MODEL=gemini-embedding-001`. Esse modelo textual compatível está
documentado no [SDK/guia oficial de embeddings Gemini](https://ai.google.dev/gemini-api/docs/embeddings).
Ele substitui o exemplo antigo `text-embedding-004` citado no briefing. O código
utiliza os tipos de tarefa de recuperação do `gemini-embedding-001` e 768 dimensões;
outros modelos podem exigir mudanças nesses parâmetros.

```powershell
.\.venv\Scripts\python.exe -m app.rag.ingest
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

As coleções Chroma são separadas por modo/modelo e hash do corpus, evitando
misturar embeddings mock com reais. Alterar documentos exige nova ingestão e
reinicialização da API. Autorizações com citações de textos alterados não passam
pela revalidação. A falta de chave ativa mock; erros de credencial presente,
quota ou rede não ativam mock automaticamente. A chave e `USE_MOCK_LLM` são
configurados no backend; o n8n não precisa de credencial Gemini.

## Variáveis

| Variável | Uso |
| --- | --- |
| `USE_MOCK_EMBEDDINGS` | Embeddings locais; sem chave o mock também é automático |
| `USE_MOCK_LLM` | Três agentes determinísticos no backend; sem chave o mock também é automático |
| `GOOGLE_API_KEY` | SDK Gemini no backend; não é necessária no mock |
| `GEMINI_EMBEDDING_MODEL` | Modelo de vetorização real |
| `GEMINI_MODEL` | Modelo dos três agentes Python |
| `GEMINI_THINKING_LEVEL` | Opcional (`minimal`, `low`, `medium`, `high`); omitido usa o padrão do modelo. Nem todo modelo aceita todo nível (a API responde 400) |
| `LLM_TIMEOUT_SECONDS` | Timeout por chamada ao SDK de agentes e embeddings, em segundos. Erros 503/504 têm 1 nova tentativa após 1 s; mantenha `2 × (2 × timeout + 1)` abaixo de 180 s |
| `DATABASE_URL` | Arquivo SQLite; `:memory:` não é aceito |
| `CHROMA_PERSIST_DIR` / `POLICY_DOCS_DIR` | Índice e documentos, relativos à raiz ou absolutos |
| `LIMITE_ALCADA_AUTOMATICA` | Limite em reais; padrão 1000,00 |
| `AUTORIZACAO_TTL_SECONDS` | Validade da autorização para iniciar uma execução |
| `APP_TIMEZONE` | Calendário do backend; padrão `America/Sao_Paulo` |
| `SEED_DEMO_DATA` | Inserção dos quatro pedidos e do estorno histórico |
| `CENTRAL_API_URL` | Endereço da API visto pelo n8n |
| `GOOGLE_DRIVE_BRONZE_FOLDER_ID` / `GOOGLE_DRIVE_SILVER_FOLDER_ID` | IDs das duas pastas no Drive |
| `GOOGLE_SHEETS_GOLD_ID` / `GOOGLE_SHEETS_GOLD_TAB` | Planilha e aba Gold |
| `EQUIPE_EMAIL` | Destino de falhas de execução sem confirmação |

## Desenvolvimento e limites do protótipo

```powershell
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -m scripts.build_workflow
node scripts/verify_workflow.cjs
.\.venv\Scripts\python.exe -m pytest
```

Node.js é opcional para o backend; habilita os quatro testes de montagem da Gold
com a resposta completa do pipeline. Sem Node, esses testes são marcados como skipped.
As fontes e a verificação estão em [docs/validacao_local.md](docs/validacao_local.md).

A automação aprova apenas arrependimento de compra online dentro da janela
configurada na política. Vício/defeito e outros motivos vão à revisão humana.
Um pedido admite um único reembolso, inclusive quando parcial. Valores parciais
divergem do valor integral nos agentes de exemplo e seguem à revisão humana.

Esta API contém dados de demonstração e não tem autenticação de usuários,
conector de pagamentos real nem processamento de comprovantes. Para operação
real, são necessários identidade autenticada, autorização por usuário e integração
financeira; o CPF informado no relato sozinho não autentica uma pessoa.
Mantenha o protótipo em ambiente local. O limite interno de alçada não altera
direitos do consumidor. Consulte as fontes oficiais na base normativa.
