const item = $input.first();
const ctx = item.json;
const prefixo = `${ctx.recebido_em.slice(0, 10)}_${ctx.origem}_${ctx.case_id}`;
const itens = [{
  json: { nome_arquivo: `${prefixo}_evento.json` },
  binary: { data: {
    data: Buffer.from(JSON.stringify(ctx.bruto, null, 2), 'utf8').toString('base64'),
    mimeType: 'application/json', fileName: `${prefixo}_evento.json`,
  } },
}];
for (const [campo, arquivo] of Object.entries(item.binary || {})) {
  const nome = String(arquivo.fileName || campo).replace(/[^a-zA-Z0-9._-]/g, '_');
  itens.push({
    json: { nome_arquivo: `${prefixo}_${campo}_${nome}` },
    binary: { data: arquivo },
  });
}
return itens;
