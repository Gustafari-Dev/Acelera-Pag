/* Verifica sintaxe e conexões sem executar nós Google ou acessar a rede. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const workflow = JSON.parse(fs.readFileSync(path.join(__dirname, '../n8n/workflow_central_reembolsos.json'), 'utf8'));
const nodes = new Map(workflow.nodes.map(node => [node.name, node]));
assert.equal(nodes.size, workflow.nodes.length, 'Nomes de nós devem ser únicos');
let expressions = 0;
function verifyExpressions(value) {
  if (typeof value === 'string' && value.startsWith('={{') && value.endsWith('}}')) {
    new Function('$json', '$', '$env', '$binary', `return (${value.slice(3, -2)});`);
    expressions++;
  } else if (Array.isArray(value)) value.forEach(verifyExpressions);
  else if (value && typeof value === 'object') Object.values(value).forEach(verifyExpressions);
}
for (const node of nodes.values()) {
  verifyExpressions(node.parameters);
  if (node.type === 'n8n-nodes-base.code') {
    new AsyncFunction('$input', '$', '$env', 'require', 'Buffer', node.parameters.jsCode);
  }
}
for (const [source, types] of Object.entries(workflow.connections)) {
  assert(nodes.has(source), `Origem desconhecida: ${source}`);
  for (const outputs of Object.values(types)) {
    for (const output of outputs) for (const edge of output) {
      assert(nodes.has(edge.node), `Destino desconhecido: ${edge.node}`);
    }
  }
}
const execution = nodes.get('Executar reembolso');
assert.equal(execution.retryOnFail, true);
assert.equal(execution.maxTries, 3);
assert.equal(execution.waitBetweenTries, 2000);
assert.equal(workflow.active, false);
assert(!workflow.nodes.some(node => node.type.startsWith('@n8n/')), 'Agentes devem ficar no Python');
assert(nodes.has('Analisar caso'));
assert(nodes.get('Analisar caso').parameters.url.includes('/casos/analisar'));

async function main() {
  if (process.argv.includes('--gold')) {
    const ctx = JSON.parse(fs.readFileSync(0, 'utf8'));
    const references = {
      'Analisar caso': ctx.analise, 'Normalizar': ctx.normalizado, 'Salvar Silver': ctx.silver,
    };
    const execute = new AsyncFunction('$input', '$', nodes.get('Montar Gold').parameters.jsCode);
    const output = await execute(
      { first: () => ({ json: ctx.final }) },
      name => ({ first: () => ({ json: references[name] }) }),
    );
    process.stdout.write(JSON.stringify(output[0].json));
  } else {
    process.stdout.write(JSON.stringify({ nodes: nodes.size, expressions, syntax: 'ok' }) + '\n');
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
