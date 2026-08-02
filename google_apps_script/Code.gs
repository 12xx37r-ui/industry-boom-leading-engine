const OUTPUT_FILES = {
  ranking: 'industry_boom_ranking.json',
  replay: 'ai_replay_2022.json',
  health: 'engine_health.json',
  macro: 'macro_context.json',
  korea: 'korea_corroboration.json',
  validation: 'model_validation.json',
  amounts: 'event_amount_quality.json',
  technology: 'technology_momentum.json',
  backtest: 'backtest_summary.json'
};

function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('산업 붐 선행예측')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function include(filename) {
  return HtmlService.createHtmlOutputFromFile(filename).getContent();
}

function getDashboardData() {
  const properties = PropertiesService.getScriptProperties();
  const baseUrl = String(properties.getProperty('OUTPUT_BASE_URL') || '').replace(/\/$/, '');
  if (!baseUrl) {
    throw new Error('Script Properties에 OUTPUT_BASE_URL을 등록해야 합니다.');
  }

  const result = {};
  Object.keys(OUTPUT_FILES).forEach(function(key) {
    const url = baseUrl + '/' + OUTPUT_FILES[key] + '?t=' + Date.now();
    const response = UrlFetchApp.fetch(url, {
      muteHttpExceptions: true,
      followRedirects: true,
      headers: { 'Cache-Control': 'no-cache' }
    });
    const status = response.getResponseCode();
    if (status >= 200 && status < 300) {
      result[key] = JSON.parse(response.getContentText('UTF-8'));
      return;
    }
    if (key === 'backtest' && status === 404) {
      result[key] = null;
      return;
    }
    throw new Error(OUTPUT_FILES[key] + ' 호출 실패 HTTP ' + status);
  });
  result.loadedAt = new Date().toISOString();
  return result;
}
