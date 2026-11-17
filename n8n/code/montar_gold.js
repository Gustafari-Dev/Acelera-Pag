const final = $input.first().json;
const analise = $('Analisar caso').first().json;
const ctx = $('Normalizar').first().json;
const erro = final.erro || analise.motivo_bloqueio || '';
const resultado = {
  case_id: ctx.caso.case_id, pedido_id: ctx.caso.pedido_id, email: ctx.caso.email,
  valor: analise.caso.valor_solicitado, recomendacao: analise.parecer_coordenador.recomendacao,
  autorizado_execucao: analise.autorizado_execucao,
  status_execucao: final.status_execucao, transaction_id: final.transaction_id || '',
  motivo_bloqueio: erro, recebido_em: analise.metadados.iniciado_em,
  bronze_ids: ctx.bronze.map(item => item.id).join(','), silver_id: $('Salvar Silver').first().json.id,
  citacoes_json: JSON.stringify(analise.parecer_politicas.citacoes),
  evidencias_json: JSON.stringify(analise.parecer_evidencias),
  resultado_json: JSON.stringify({ analise, execucao: final }),
};
const destino = final.status_execucao === 'NAO_CONFIRMADO' ? (ctx.config.equipe_email || ctx.caso.email) : ctx.caso.email;
let mensagem;
if (final.status_execucao === 'CONCLUIDO') mensagem = `Reembolso simulado confirmado. Transação: ${final.transaction_id}.`;
else if (final.status_execucao === 'NAO_CONFIRMADO') mensagem = 'Execução sem confirmação. A equipe deve conciliar o histórico do pedido antes de abrir outra operação.';
else mensagem = `Caso encaminhado: ${analise.parecer_coordenador.recomendacao}. ${analise.parecer_coordenador.justificativa}`;
return [{ json: { ...resultado, destinatario: destino, mensagem_email: `Caso ${ctx.caso.case_id}. ${mensagem}` } }];
