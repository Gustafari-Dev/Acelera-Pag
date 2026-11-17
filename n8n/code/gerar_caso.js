// Um evento por execução. O payload original permanece separado dos dados normalizados.
const { randomUUID } = require('crypto');
const item = $input.first();
const bruto = item.json.body ?? item.json;
const config = {
  api_url: ($env.CENTRAL_API_URL || 'http://host.docker.internal:8000').replace(/\/$/, ''),
  bronze_folder: $env.GOOGLE_DRIVE_BRONZE_FOLDER_ID || '',
  silver_folder: $env.GOOGLE_DRIVE_SILVER_FOLDER_ID || '',
  gold_sheet: $env.GOOGLE_SHEETS_GOLD_ID || '',
  gold_tab: $env.GOOGLE_SHEETS_GOLD_TAB || 'Reembolsos',
  equipe_email: $env.EQUIPE_EMAIL || '',
};
for (const key of ['bronze_folder', 'silver_folder', 'gold_sheet']) {
  if (!config[key]) throw new Error(`Configuração obrigatória ausente: ${key}`);
}
return [{ json: {
  bruto, case_id: bruto.case_id || randomUUID(), config,
  recebido_em: new Date().toISOString(), origem: 'webhook',
}, binary: item.binary }];
