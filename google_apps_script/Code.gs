const DASHBOARD_RELEASE = '6.2.0-gas-dashboard';
const CACHE_SECONDS = 300;
const DASHBOARD_PATHS = Object.freeze({
  live: 'outputs/v61_prospective_policy/v61_dashboard_payload.json',
  scorecard: 'outputs/v61_prospective_policy/v61_prospective_scorecard.json',
  registry: 'outputs/v61_prospective_policy/v61_policy_registry.json',
  challenger: 'outputs/v60_champion_challenger/v60_champion_challenger_comparison.json',
  historical: 'outputs/v51_historical_audit/v51_historical_audit.json',
  outcomes: 'outputs/v50_final_validator/v50_prospective_scorecard.json'
});

function doGet() {
  return HtmlService.createTemplateFromFile('Index')
    .evaluate()
    .setTitle('산업 붐 선행예측 V6.2')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/**
 * Script Properties 중 하나를 설정합니다.
 *
 * 공개 저장소 권장값:
 *   GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH(main)
 *
 * 비공개 저장소 추가값:
 *   GITHUB_TOKEN (Contents: Read)
 *
 * 대체 방식:
 *   OUTPUT_BASE_URL=https://raw.githubusercontent.com/<owner>/<repo>/<branch>
 */
function getDashboardData(forceRefresh) {
  if (forceRefresh === true) clearDashboardCache_();

  const source = getSourceConfig_();
  const live = fetchJson_(source, DASHBOARD_PATHS.live, true);
  const scorecard = fetchJson_(source, DASHBOARD_PATHS.scorecard, false) || {};
  const registry = fetchJson_(source, DASHBOARD_PATHS.registry, false) || { snapshots: [] };
  const challenger = fetchJson_(source, DASHBOARD_PATHS.challenger, false) || {};
  const historical = fetchJson_(source, DASHBOARD_PATHS.historical, false) || {};
  const outcomes = fetchJson_(source, DASHBOARD_PATHS.outcomes, false) || {};

  const snapshots = Array.isArray(registry.snapshots) ? registry.snapshots.slice() : [];
  snapshots.sort(function(a, b) {
    return String(a.snapshot_id || '').localeCompare(String(b.snapshot_id || ''));
  });

  let previousSnapshot = null;
  if (snapshots.length >= 2) {
    const previousId = snapshots[snapshots.length - 2].snapshot_id;
    previousSnapshot = fetchJson_(
      source,
      'prospective_history/v61_policy_snapshots/' + encodeURIComponent(previousId) + '.json',
      false
    );
  }

  const previousRanks = {};
  if (previousSnapshot && Array.isArray(previousSnapshot.decisions)) {
    previousSnapshot.decisions.forEach(function(row) {
      previousRanks[String(row.theme_id || '')] = Number(row.rank);
    });
  }

  const top20 = (Array.isArray(live.top_20) ? live.top_20 : []).map(function(row) {
    const currentRank = Number(row.rank);
    const previousRank = previousRanks[String(row.theme_id || '')];
    return {
      rank: currentRank,
      previous_rank: Number.isFinite(previousRank) ? previousRank : null,
      rank_change: Number.isFinite(previousRank) ? previousRank - currentRank : null,
      is_new_entry: Object.keys(previousRanks).length > 0 && !Number.isFinite(previousRank),
      theme_id: row.theme_id || '',
      theme_name: row.theme_name || row.theme_id || '-',
      sector: row.sector || '-',
      candidate_stage: row.candidate_stage || '-',
      predicted_score: nullableNumber_(row.predicted_score),
      direct_commercialization_score: nullableNumber_(row.direct_commercialization_score),
      phase3_investment_score: nullableNumber_(row.phase3_investment_score),
      source_diffusion_percent: nullableNumber_(row.source_diffusion_percent),
      champion_live_alert: row.champion_live_alert === true,
      challenger_live_alert: row.challenger_live_alert === true,
      added_by_challenger: row.added_by_challenger === true,
      boom_score: row.boom_score == null ? null : nullableNumber_(row.boom_score)
    };
  });

  const v60Benchmark = challenger.benchmark || {};
  const v60Blind = challenger.blind_holdout || {};
  const v51Benchmark = historical.benchmark || {};
  const v51Blind = historical.sealed_blind_holdout || {};

  return {
    release: DASHBOARD_RELEASE,
    status: live.status || 'UNKNOWN',
    as_of: live.as_of || '',
    loaded_at: new Date().toISOString(),
    source: {
      mode: source.mode,
      repository: source.repository,
      branch: source.branch,
      private_token_used: source.hasToken
    },
    progress: live.progress || {},
    alerts: {
      champion: Number(live.champion_live_alert_count || 0),
      challenger: Number(live.challenger_live_alert_count || 0),
      added_count: Array.isArray(live.added_theme_ids) ? live.added_theme_ids.length : 0,
      added_theme_ids: Array.isArray(live.added_theme_ids) ? live.added_theme_ids : []
    },
    next_evaluation_due: live.next_evaluation_due || null,
    top_20: top20,
    snapshot: {
      count: snapshots.length,
      current_id: snapshots.length ? snapshots[snapshots.length - 1].snapshot_id : null,
      previous_id: snapshots.length >= 2 ? snapshots[snapshots.length - 2].snapshot_id : null,
      rank_change_available: Object.keys(previousRanks).length > 0
    },
    validation: {
      historical: {
        champion: (v60Benchmark.champion || (v51Benchmark.metrics || {})),
        challenger: v60Benchmark.challenger || null,
        delta: v60Benchmark.delta || null,
        champion_gate_passed: v51Benchmark.gate ? v51Benchmark.gate.passed === true : null
      },
      blind: {
        champion: (v60Blind.champion || (v51Blind.metrics || {})),
        challenger: v60Blind.challenger || null,
        delta: v60Blind.delta || null,
        champion_gate_passed: v51Blind.gate ? v51Blind.gate.passed === true : null
      },
      research_gate_passed: challenger.research_gate ? challenger.research_gate.passed === true : null,
      changed_cases: Array.isArray(challenger.changed_cases) ? challenger.changed_cases : []
    },
    prospective: {
      status: scorecard.status || outcomes.status || 'UNKNOWN',
      horizons: scorecard.horizons || {},
      minimum_maturity_checks: scorecard.minimum_maturity_checks || [],
      promotion_evaluable: scorecard.promotion_evaluable === true,
      automatic_promotion_allowed: scorecard.automatic_promotion_allowed === true,
      market_outcomes: outcomes.horizon_metrics || {},
      investment_use_allowed: outcomes.investment_use_allowed === true
    },
    warnings: [
      '현재 순위는 미래검증 전 사전검증(prevalidation) 후보순위입니다.',
      'V0.9.1 boom_score 계산식은 동결되어 있으며 현재 live top20의 boom_score는 null입니다.',
      'Challenger는 미래 6·12·24개월 독립성과가 쌓이기 전 자동 승격되지 않습니다.'
    ]
  };
}

function diagnoseDashboardConnection() {
  const source = getSourceConfig_();
  const started = Date.now();
  const payload = fetchJson_(source, DASHBOARD_PATHS.live, true, true);
  return {
    ok: true,
    release: DASHBOARD_RELEASE,
    source_mode: source.mode,
    repository: source.repository,
    branch: source.branch,
    http_target: source.mode === 'github-api' ? 'GitHub Contents API' : 'GitHub Raw',
    payload_status: payload.status || 'UNKNOWN',
    payload_as_of: payload.as_of || null,
    elapsed_ms: Date.now() - started
  };
}

function clearDashboardCache() {
  clearDashboardCache_();
  return { ok: true, cleared_at: new Date().toISOString() };
}

function getSourceConfig_() {
  const props = PropertiesService.getScriptProperties();
  const token = String(props.getProperty('GITHUB_TOKEN') || '').trim();
  const owner = String(props.getProperty('GITHUB_OWNER') || '').trim();
  const repo = String(props.getProperty('GITHUB_REPO') || 'industry-boom-leading-engine').trim();
  const branch = String(props.getProperty('GITHUB_BRANCH') || 'main').trim();
  let baseUrl = String(props.getProperty('OUTPUT_BASE_URL') || '').trim().replace(/\/$/, '');

  // 예전 OUTPUT_BASE_URL이 outputs/... 폴더를 가리키는 경우 저장소 루트로 자동 보정합니다.
  baseUrl = baseUrl.replace(/\/outputs\/.*$/i, '');

  if (token && owner && repo) {
    return {
      mode: 'github-api',
      owner: owner,
      repo: repo,
      branch: branch,
      token: token,
      hasToken: true,
      repository: owner + '/' + repo
    };
  }

  if (baseUrl) {
    return {
      mode: 'raw-base-url',
      baseUrl: baseUrl,
      branch: branch,
      hasToken: false,
      repository: owner && repo ? owner + '/' + repo : baseUrl
    };
  }

  if (owner && repo) {
    return {
      mode: 'raw-public',
      baseUrl: 'https://raw.githubusercontent.com/' + encodeURIComponent(owner) + '/' + encodeURIComponent(repo) + '/' + encodeURIComponent(branch),
      branch: branch,
      hasToken: false,
      repository: owner + '/' + repo
    };
  }

  throw new Error(
    'GAS Script Properties 설정이 없습니다. ' +
    '공개 저장소는 GITHUB_OWNER·GITHUB_REPO·GITHUB_BRANCH를, ' +
    '비공개 저장소는 여기에 GITHUB_TOKEN(Contents: Read)을 추가하세요.'
  );
}

function fetchJson_(source, relativePath, required, noCache) {
  const cache = CacheService.getScriptCache();
  const cacheKey = cacheKey_(source, relativePath);
  if (!noCache) {
    const cached = cache.get(cacheKey);
    if (cached) {
      try { return JSON.parse(cached); } catch (ignore) {}
    }
  }

  let url;
  const headers = {
    'Accept': 'application/json',
    'Cache-Control': 'no-cache',
    'User-Agent': 'industry-boom-gas-dashboard/' + DASHBOARD_RELEASE
  };

  if (source.mode === 'github-api') {
    const encodedPath = relativePath.split('/').map(encodeURIComponent).join('/');
    url = 'https://api.github.com/repos/' + encodeURIComponent(source.owner) + '/' + encodeURIComponent(source.repo) + '/contents/' + encodedPath + '?ref=' + encodeURIComponent(source.branch);
    headers.Authorization = 'Bearer ' + source.token;
    headers.Accept = 'application/vnd.github.raw+json';
  } else {
    url = source.baseUrl.replace(/\/$/, '') + '/' + relativePath.split('/').map(encodeURIComponent).join('/');
  }

  const response = UrlFetchApp.fetch(url + (url.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now(), {
    method: 'get',
    muteHttpExceptions: true,
    followRedirects: true,
    headers: headers
  });
  const status = response.getResponseCode();
  if (status < 200 || status >= 300) {
    if (required) {
      throw new Error(relativePath + ' 호출 실패 HTTP ' + status + '. GitHub 설정과 Actions 결과 커밋 여부를 확인하세요.');
    }
    return null;
  }

  try {
    const result = JSON.parse(response.getContentText('UTF-8'));
    const serialized = JSON.stringify(result);
    if (serialized.length < 95000) cache.put(cacheKey, serialized, CACHE_SECONDS);
    return result;
  } catch (error) {
    if (required) throw new Error(relativePath + ' JSON 해석 실패: ' + error.message);
    return null;
  }
}

function cacheKey_(source, relativePath) {
  const raw = [DASHBOARD_RELEASE, source.mode, source.repository, source.branch, relativePath].join('|');
  const digest = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, raw, Utilities.Charset.UTF_8);
  return 'ible_' + Utilities.base64EncodeWebSafe(digest).replace(/=+$/, '').slice(0, 36);
}

function clearDashboardCache_() {
  // 키 목록 조회 API가 없으므로 릴리즈별 캐시를 만료시키기 위해 모든 캐시를 제거합니다.
  CacheService.getScriptCache().removeAll([
    cacheKeySafe_(DASHBOARD_PATHS.live),
    cacheKeySafe_(DASHBOARD_PATHS.scorecard),
    cacheKeySafe_(DASHBOARD_PATHS.registry),
    cacheKeySafe_(DASHBOARD_PATHS.challenger),
    cacheKeySafe_(DASHBOARD_PATHS.historical),
    cacheKeySafe_(DASHBOARD_PATHS.outcomes)
  ]);
}

function cacheKeySafe_(path) {
  try { return cacheKey_(getSourceConfig_(), path); } catch (error) { return 'ible_missing_' + path.length; }
}

function nullableNumber_(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}
