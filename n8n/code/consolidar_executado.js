const ctx = $('Analisar caso').first().json;
const recibo = $('Executar reembolso').first().json;
const pedido = $input.first().json;
const salvo = pedido.historico_estornos?.find(item => item.transaction_id === recibo.transaction_id);
if (!salvo || salvo.status !== 'CONCLUIDO' || salvo.case_id !== ctx.caso.case_id ||
    salvo.pedido_id !== ctx.caso.pedido_id || recibo.case_id !== ctx.caso.case_id ||
    Number(salvo.valor) !== Number(ctx.caso.valor_solicitado) ||
    Number(recibo.valor) !== Number(ctx.caso.valor_solicitado) || recibo.status !== 'CONCLUIDO') {
  throw new Error('Recibo de execução não confirmado no histórico oficial.');
}
return [{ json: { status_execucao: 'CONCLUIDO', transaction_id: salvo.transaction_id, erro: null } }];
