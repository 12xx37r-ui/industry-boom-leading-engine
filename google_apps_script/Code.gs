const DASHBOARD_FILE = 'v50_dashboard_payload.json';

function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('산업 붐 선행예측 V5')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function getDashboardData() {
  const properties = PropertiesService.getScriptProperties();
  const baseUrl = String(properties.getProperty('OUTPUT_BASE_URL') || '').replace(/\/$/, '');
  if (!baseUrl) throw new Error('Script Properties에 OUTPUT_BASE_URL을 등록해야 합니다.');
  const response = UrlFetchApp.fetch(baseUrl + '/' + DASHBOARD_FILE + '?t=' + Date.now(), {
    muteHttpExceptions: true,
    followRedirects: true,
    headers: { 'Cache-Control': 'no-cache' }
  });
  const status = response.getResponseCode();
  if (status < 200 || status >= 300) throw new Error(DASHBOARD_FILE + ' 호출 실패 HTTP ' + status);
  const result = JSON.parse(response.getContentText('UTF-8'));
  result.loadedAt = new Date().toISOString();
  return result;
}
