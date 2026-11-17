# Configuração do workflow n8n

Importe `workflow_central_reembolsos.json` pelo menu **Import from File**. São
17 nós. O arquivo está desativado e não
contém credenciais, tokens ou identificadores privados de pastas.

O nó **Analisar caso** envia somente o caso Silver para `POST /casos/analisar`.
O backend consulta o pedido e o RAG, executa os três agentes e o validador e salva
a autorização. Toda a lógica de raciocínio fica no código Python. O n8n recebe
o resultado pronto e cuida de arquivos, roteamento, retentativas e comunicação.

## Ambiente local

Em uma instância n8n existente, configure as variáveis abaixo no processo que a
inicia. O `.env` da API não é lido automaticamente pelo n8n.

```dotenv
CENTRAL_API_URL=http://127.0.0.1:8000
GOOGLE_DRIVE_BRONZE_FOLDER_ID=ID_DA_PASTA_BRONZE
GOOGLE_DRIVE_SILVER_FOLDER_ID=ID_DA_PASTA_SILVER
GOOGLE_SHEETS_GOLD_ID=ID_DA_PLANILHA
GOOGLE_SHEETS_GOLD_TAB=Reembolsos
EQUIPE_EMAIL=seu-email-de-teste@example.com
N8N_BLOCK_ENV_ACCESS_IN_NODE=false
NODE_FUNCTION_ALLOW_BUILTIN=crypto
```

Configure `USE_MOCK_LLM`, `GOOGLE_API_KEY` e o modelo no `.env` do **backend**;
essas configurações não são necessárias no processo do n8n.
Substitua os IDs e o destinatário pelos recursos de teste da sua conta. O nó
`Gerar caso` verifica os três IDs antes de processar a entrada. Não coloque a chave
Gemini no workflow: ela é usada somente pelo backend. O uso de `crypto`
fica limitado à geração do UUID, conforme a [configuração oficial de módulos no
Code node](https://docs.n8n.io/hosting/configuration/configuration-examples/modules-in-code-node/).
Em instalações com task runners externos, configure essa permissão também no
runner. Instâncias que bloqueiam `$env` podem usar valores fixos não secretos no
nó `Gerar caso` e no campo de modelo, conforme as regras da própria instância.

Para uma instalação nativa de desenvolvimento, consulte o [guia oficial do
n8n](https://docs.n8n.io/hosting/installation/npm/) sobre versões de Node suportadas
e métodos de instalação. Antes de iniciar o n8n na mesma janela PowerShell:

```powershell
$env:CENTRAL_API_URL = 'http://127.0.0.1:8000'
$env:GOOGLE_DRIVE_BRONZE_FOLDER_ID = 'ID_DA_PASTA_BRONZE'
$env:GOOGLE_DRIVE_SILVER_FOLDER_ID = 'ID_DA_PASTA_SILVER'
$env:GOOGLE_SHEETS_GOLD_ID = 'ID_DA_PLANILHA'
$env:GOOGLE_SHEETS_GOLD_TAB = 'Reembolsos'
$env:EQUIPE_EMAIL = 'seu-email-de-teste@example.com'
$env:N8N_BLOCK_ENV_ACCESS_IN_NODE = 'false'
$env:NODE_FUNCTION_ALLOW_BUILTIN = 'crypto'
```

Para Docker Desktop, passe essas variáveis ao contêiner/runner e use
`CENTRAL_API_URL=http://host.docker.internal:8000`. Inicie a API local com
`--host 0.0.0.0`; a instalação do n8n e o acesso ao firewall dependem do ambiente.

## Credenciais e recursos Google

1. Crie duas pastas de teste no Drive, **Bronze** e **Silver**, e uma planilha
   **Gold** com aba `Reembolsos`. Copie os IDs das URLs para as variáveis.
2. Configure credenciais OAuth Google Drive, Google Sheets e Gmail no n8n,
   usando o callback exibido na própria credencial. Habilite as APIs
   correspondentes no projeto Google e conceda acesso aos recursos de teste.
3. Vincule Drive a `Salvar Bronze` e `Salvar Silver`, Sheets a `Salvar Gold` e
   Gmail a `Enviar resposta`. O JSON não tenta inventar credenciais ausentes.
4. Para agentes reais, configure `USE_MOCK_LLM=false` e `GOOGLE_API_KEY` no `.env`
   da API e reinicie Uvicorn. Não há nós de modelo ou credencial Gemini no n8n.
   Drive, Sheets e Gmail continuam sendo serviços externos reais em ambos os modos.
5. Revise o destinatário nos exemplos antes de enviar eventos. O endereço
   `cliente@example.com` é ilustrativo. Use seu próprio e-mail nos testes.

Crie esta linha de cabeçalho na planilha, na ordem abaixo:

```text
case_id,pedido_id,email,valor,recomendacao,autorizado_execucao,status_execucao,transaction_id,motivo_bloqueio,recebido_em,bronze_ids,silver_id,citacoes_json,evidencias_json,resultado_json
```

`Salvar Gold` usa **Append or Update Row**, correspondência por `case_id` e
formatação **RAW**. Isso permite atualizar um caso repetido sem interpretar
conteúdo de cliente como fórmula da planilha. O envio de e-mail não possui
garantia de exatamente uma entrega: retomar manualmente uma execução pode
reenviá-lo. A idempotência financeira está no SQLite.

## Percurso dos dados

| Etapa | Nós e comportamento |
| --- | --- |
| Entrada | `Entrada` recebe POST em `/central-reembolsos`; `Gerar caso` preserva o corpo original e cria UUID se ausente |
| Bronze | `Preparar Bronze` produz o JSON bruto e um item por arquivo binário; `Salvar Bronze` confirma IDs no Drive |
| Silver | `Normalizar` agrega os IDs Bronze, padroniza valores e mantém ausências; `Salvar Silver` armazena `{schema_version, caso, referencias_bronze}` |
| Análise | `Analisar caso` chama POST `/casos/analisar` com o caso normalizado; o Python busca pedido/RAG, executa os agentes e valida |
| Resposta | O JSON inclui caso, pedido oficial, três pareceres, validação e metadados; a análise e a autorização já estão persistidas |
| Execução | `Autorizado?` exige booleano verdadeiro; `Executar reembolso` chama a API com o mesmo UUID e valor em todas as tentativas |
| Conferência | `Verificar historico` relê o pedido; `Consolidar executado` exige a transação concluída no histórico, com caso e valor correspondentes |
| Gold | Os caminhos de sucesso, bloqueio e falha convergem em `Montar Gold`, depois `Salvar Gold` |
| Comunicação | `Enviar resposta` usa texto simples; `Responder webhook` retorna o resultado consolidado |

Os anexos originais são preservados quando chegam como propriedades binárias do
Webhook. Para JSON puro, o corpo original é a evidência Bronze. Links para
anexos permanecem no JSON bruto e não são baixados automaticamente. Não há OCR;
o integrador precisa fornecer os campos extraídos e a classificação do anexo.
Uma classificação `OK` recebida é usada como dado do protótipo, sem certificar
a autenticidade do arquivo. Forms e Gmail podem alimentar esse Webhook por um
adaptador que preserve o mesmo contrato.

## Retentativas e falhas

`Executar reembolso` está configurado com `retryOnFail=true`, `maxTries=3` e
`waitBetweenTries=2000`: no máximo três tentativas totais, espera fixa de dois
segundos. Esse é o comportamento [documentado pelo
n8n](https://docs.n8n.io/integrations/builtin/rate-limits/), sem backoff exponencial.
O retry nativo também pode repetir erros permanentes; as travas e a idempotência
da API impedem que isso produza uma nova transação.

O `PED-004` registra a primeira tentativa autorizada no SQLite e responde 503
sem criar reembolso. A segunda tentativa conclui. O contador sobrevive à
reinicialização. A nova leitura do histórico é obrigatória para confirmar sucesso.

Se as tentativas se esgotarem ou a conferência falhar, a saída de erro segue para
`Consolidar falha`: o status Gold será `NAO_CONFIRMADO`, sem alegar conclusão, e
o e-mail vai à equipe, quando configurada. Isso também cobre perda da resposta
após uma execução que possa ter sido persistida: concilie o histórico pelo UUID.
Bloqueios seguem como `BLOQUEADO`. Falha anterior à validação interrompe o fluxo
sem tentar executar. Erro de Sheets ou Gmail interrompe a comunicação e deve ser
retomado após verificar o estado já persistido.

## Testar e alterar

1. Inicie a API e configure as credenciais.
2. Rode `python -m scripts.export_examples` para gerar entradas com datas atuais
   do banco. Abra os JSONs e substitua o e-mail pelo seu endereço de teste.
3. Clique **Listen for test event** no Webhook e envie um exemplo conforme o
   comando PowerShell no README principal.
4. Confira os arquivos Bronze/Silver, o registro Gold e o histórico em
   `GET /pedidos/PED-00N`. Para falha temporária, confira as duas tentativas.
5. Depois da conferência, ative o workflow e use a URL de produção do Webhook.

Edite os prompts em `app/agents/prompts.py`, o pipeline em `app/agents/pipeline.py`,
o código de integração em `n8n/code/` ou os schemas Pydantic e execute:

```powershell
.\.venv\Scripts\python.exe -m scripts.build_workflow
node scripts/verify_workflow.cjs
.\.venv\Scripts\python.exe -m pytest
```

A geração é determinística. Reimporte o JSON gerado e confira as credenciais
da cópia importada. O n8n pode alterar telas/parâmetros entre versões; o arquivo
inclui versões explícitas dos nós. A sintaxe, as conexões e a montagem da Gold foram
testadas localmente com as respostas do pipeline; a execução dentro de uma instância n8n com Google Workspace
precisa ser conferida após vincular suas credenciais.
