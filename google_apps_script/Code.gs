const OUTPUT_FILES = {
  current: 'v2_shadow_current.json',
  summary: 'v2_shadow_summary.json',
  ledger: 'v2_shadow_ledger.json',
  queue: 'v2_shadow_scorecard_queue.json',
  universe: 'v2_1_theme_universe_status.json',
  backlog: 'v2_1_data_backlog.json',
  nextGate: 'v2_next_gate.json'
};

function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('산업 붐 선행예측 V2.1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function getDashboardData() {
  const properties = PropertiesService.getScriptProperties();
  const baseUrl = String(properties.getProperty('OUTPUT_BASE_URL') || '').replace(/\/$/, '');
  if (!baseUrl) throw new Error('Script Properties에 OUTPUT_BASE_URL을 등록해야 합니다.');

  const result = {};
  Object.keys(OUTPUT_FILES).forEach(function(key) {
    const response = UrlFetchApp.fetch(baseUrl + '/' + OUTPUT_FILES[key] + '?t=' + Date.now(), {
      muteHttpExceptions: true,
      followRedirects: true,
      headers: { 'Cache-Control': 'no-cache' }
    });
    const status = response.getResponseCode();
    if (status < 200 || status >= 300) throw new Error(OUTPUT_FILES[key] + ' 호출 실패 HTTP ' + status);
    result[key] = JSON.parse(response.getContentText('UTF-8'));
  });
  result.loadedAt = new Date().toISOString();
  return result;
}
