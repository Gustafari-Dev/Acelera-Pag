const ctx = $('Gerar caso').first().json;
const bronze = $input.all().map(item => ({ id: item.json.id, nome: item.json.name }));
if (bronze.some(item => !item.id)) throw new Error('Upload Bronze sem ID confirmado.');
const raw = ctx.bruto;
const textoValor = String(raw.valor_solicitado ?? '').trim();
if (!/^\d+(?:[.,]\d{1,2})?$/.test(textoValor)) {
  throw new Error('valor_solicitado deve usar reais sem separador de milhares.');
}
const pedidoId = String(raw.pedido_id || '').trim().toUpperCase();
if (!/^PED-\d{3}$/.test(pedidoId)) throw new Error('pedido_id inválido.');
const caso = {
  case_id: ctx.case_id, pedido_id: pedidoId,
  cliente_id: raw.cliente_id == null ? null : String(raw.cliente_id).trim(),
  cliente_nome: raw.cliente_nome == null ? null : String(raw.cliente_nome).trim(),
  cpf: raw.cpf == null ? null : String(raw.cpf).replace(/\D/g, ''),
  email: String(raw.email || '').trim(),
  valor_solicitado: textoValor.replace(',', '.'),
  motivo: String(raw.motivo || 'OUTRO').trim().toUpperCase(),
  relato: String(raw.relato || '').trim(),
  data_entrega_informada: raw.data_entrega_informada || null,
  anexo_status: String(raw.anexo_status || 'AUSENTE').trim().toUpperCase(),
};
// Não preencher lacunas usando o pedido oficial: isso esconderia ausência de evidência.
const silver = { schema_version: 'v1', caso, referencias_bronze: bronze };
return [{
  json: { ...ctx, caso, bronze },
  binary: { data: {
    data: Buffer.from(JSON.stringify(silver, null, 2), 'utf8').toString('base64'),
    mimeType: 'application/json', fileName: `${ctx.case_id}_silver.json`,
  } },
}];
