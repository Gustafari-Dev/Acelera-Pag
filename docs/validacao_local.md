# Validação local

Execução em 16/11/2026, Windows, Python 3.12.13. Dependências resolvidas em
`requirements.lock.txt`: FastAPI 0.163.1, Pydantic 2.14.3, ChromaDB 1.5.9,
google-genai 2.34.0 e pytest 9.2.0. O ambiente foi sincronizado com o lock;
LangChain, LangGraph e seus pacotes auxiliares foram removidos.

## Verificações reproduzíveis

```powershell
.\.venv\Scripts\python.exe -m app.rag.ingest
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m scripts.demo
.\.venv\Scripts\python.exe -m scripts.build_workflow
node scripts/verify_workflow.cjs
.\.venv\Scripts\python.exe -m ruff check app scripts tests
.\.venv\Scripts\python.exe -m compileall -q app scripts tests
```

| Verificação | Resultado |
| --- | --- |
| Ingestão Chroma offline | Sete seções, com metadados e texto preservados |
| Suíte pytest | 75 testes aprovados, sem chamadas externas |
| Workflow JSON | 17 nós, 31 expressões; sintaxe JavaScript e destinos de conexões válidos |
| Integração n8n | Recebe os três pareceres do Python; montagem da Gold testada nos quatro cenários |
| Uvicorn via HTTP local | Script `scripts.demo --api-url ...` chamou `/casos/analisar` nos quatro pedidos com banco isolado |
| Adaptador de embeddings real | Contrato do SDK, tipos de tarefa e dimensão verificados com cliente duplo |
| Agentes Gemini | Quatro cenários, respostas Pydantic/dicionário/texto, schema inválido e falha de rede |
| SDK oficial de geração | Serialização de response_schema e parsing dos três modelos, interceptando apenas o HTTP |

## Quatro cenários

| Pedido | Parecer | Validador | Execução simulada |
| --- | --- | --- | --- |
| PED-001 | APROVAR | Autorizado | HTTP 200 e UUID de transação |
| PED-002 | REJEITAR | Bloqueado por estorno anterior | Nova execução retorna 409 |
| PED-003 | REVISAO_HUMANA | Bloqueado por divergência | Execução retorna 403 |
| PED-004 | APROVAR | Autorizado | HTTP 503, depois 200; repetição retorna o mesmo UUID |

Os testes também exercitam citação inventada/modificada, falta de citação,
snapshot adulterado, identidade divergente, extrapolação da alçada, mudança do
pedido depois da autorização, expiração, valores inválidos, campos extras,
limites de zero/sete/oito dias, entrega futura, corrida entre dois casos e
persistência do contador de falha após reinicialização.
O novo endpoint POST `/casos/analisar` executa os três agentes e o validador,
persiste a análise completa e autoriza a execução nos cenários válidos. As
retentativas do mesmo UUID retornam o mesmo parecer sem repetir o LLM. Falta de
chave ativa o fallback; erro de rede, JSON inválido ou geração interrompida no modo
real retorna HTTP 502 sem criar autorização.

Para conferir uma API já iniciada no PowerShell, sem executar estornos:

```powershell
.\.venv\Scripts\python.exe -m scripts.demo --api-url http://127.0.0.1:8000
```

O comando obtém as datas e identidades oficiais e cria UUIDs novos em cada
chamada. `--executar` também testa a execução financeira simulada e o retry.
Sem `--api-url`, a demonstração roda integralmente em banco temporário e mock.

## Integrações a conferir com credenciais

Não foram realizadas chamadas reais ao Gemini nem executado o workflow dentro
de uma instância n8n conectada a Drive, Sheets e Gmail. O JSON e os prompts
estão preparados para importação, com as configurações em `n8n/README.md`.
Os testes locais do modo real interceptam a resposta/HTTP do SDK e não medem qualidade de LLM ou
qualidade semântica dos embeddings Gemini. A comprovação Bronze/Silver/Gold
e o recebimento de e-mail dependem da execução na conta configurada.
