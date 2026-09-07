// crawl-watchdog — 수집 크론 누락·실패 감지 후 workflow_dispatch 로 복구 요청.
// V2(wavyhairs/nuclens-v2) 원본의 V1 적응판(2026-09-07, 5차 이식):
//   - V2 의 crawl_runs.json 원장(슬롯 claim·zero_articles 구분)은 V1 crawl.yml 에
//     없는 프로토콜이라 제거 — Actions run 목록만으로 판정한다. 그 대가로
//     '성공했지만 0건'과 '조용한 미확정 성공'은 구분 못 한다(원장 도입 시 복원).
//   - V2 에 없는 일일 dispatch 상한을 추가 — 실패 반복 시 15분마다 재발화하면
//     Gemini 무료 쿼터(쿼터 체인 1주 게이트 관찰 중)를 굶긴다.
//   - 복구 시 notify.yml 로 동향봇 채널에 알린다(장애 보고 규약, 2026-09-01).
// V1 crawl cron 은 "0 */3 * * *" 라 V2 와 같은 3시간 슬롯 수학을 그대로 쓴다.

const SLOT_MS = 3 * 60 * 60 * 1000;
const DAY_MS = 24 * 60 * 60 * 1000;
// ponytail: 상한 4회/일 — 3시간 슬롯 8개의 절반. 그 이상 실패하면 재실행이 아니라
// 사람이 볼 문제다. 조정은 wrangler.jsonc 의 MAX_DISPATCHES_PER_DAY.
const DEFAULT_MAX_DISPATCHES_PER_DAY = 4;

export function slotStart(now) {
  return new Date(Math.floor(now.getTime() / SLOT_MS) * SLOT_MS);
}

export function evaluateRuns(runs, now = new Date(), graceMinutes = 25) {
  const start = slotStart(now);
  const graceEnds = new Date(start.getTime() + graceMinutes * 60_000);
  const current = runs.filter((run) => {
    const created = new Date(run.created_at || 0);
    return created >= start;
  });
  const active = current.find((run) => run.status === "queued" || run.status === "in_progress");
  if (active) return { shouldDispatch: false, state: "workflow_active", start };

  const succeeded = current.find((run) =>
    run.status === "completed" && run.conclusion === "success");
  if (succeeded) return { shouldDispatch: false, state: "workflow_success", start };

  const failed = current.find((run) => run.status === "completed" && run.conclusion !== "success");
  if (failed) return { shouldDispatch: true, state: "workflow_failed", start };

  if (now < graceEnds) return { shouldDispatch: false, state: "within_schedule_grace", start };
  return { shouldDispatch: true, state: "trigger_missing", start };
}

// 최근 24시간에 **watchdog 이 쏜** 복구 run 수. 수동 실행은 세지 않는다 —
// 2026-09-07 실측: 다른 세션의 수동 실행 5건이 상한(4)을 먹어 정작 12시 슬롯
// 누락을 못 살렸다. API run 목록엔 inputs 가 없으므로 crawl.yml 의 run-name 이
// 제목에 찍어 주는 trigger_source 를 읽는다.
export const RECOVERY_MARKER = "backup_watchdog";

export function dispatchCountLast24h(runs, now = new Date()) {
  const cutoff = new Date(now.getTime() - DAY_MS);
  return runs.filter((run) =>
    run.event === "workflow_dispatch"
    && String(run.display_title || run.name || "").includes(RECOVERY_MARKER)
    && new Date(run.created_at || 0) >= cutoff).length;
}

async function github(env, path, init = {}) {
  const response = await fetch(`https://api.github.com${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "User-Agent": "nuclens-crawl-watchdog/1.0",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
  if (!response.ok) {
    throw new Error(`GitHub ${init.method || "GET"} ${path}: HTTP ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

async function notify(env, base, text) {
  // 알림 실패가 복구 실패로 번지지 않게 비치명 — 로그만 남긴다.
  try {
    const workflow = env.NOTIFY_WORKFLOW || "notify.yml";
    await github(env, `${base}/actions/workflows/${workflow}/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: "main", inputs: { message: text } }),
    });
  } catch (error) {
    console.log(JSON.stringify({ notify_error: String(error) }));
  }
}

export async function checkAndRecover(env, now = new Date(), { dryRun = false } = {}) {
  if (!env.GITHUB_TOKEN) throw new Error("GITHUB_TOKEN Worker secret is missing");
  const owner = env.GITHUB_OWNER || "policy174";
  const repo = env.GITHUB_REPO || "nuclear-news";
  const workflow = env.GITHUB_WORKFLOW || "crawl.yml";
  const grace = Number.parseInt(env.BACKUP_GRACE_MINUTES || "25", 10);
  const cap = Number.parseInt(
    env.MAX_DISPATCHES_PER_DAY || String(DEFAULT_MAX_DISPATCHES_PER_DAY), 10);
  const repoBase = `/repos/${owner}/${repo}`;
  const base = `${repoBase}/actions/workflows/${workflow}`;
  const data = await github(env, `${base}/runs?branch=main&per_page=30`);
  const runs = Array.isArray(data?.workflow_runs) ? data.workflow_runs : [];
  const decision = evaluateRuns(runs, now, grace);
  const log = {
    watchdog_state: decision.state,
    slot: decision.start.toISOString(),
    checked_at: now.toISOString(),
    dispatched: false,
  };
  if (!decision.shouldDispatch) {
    console.log(JSON.stringify(log));
    return log;
  }

  const recent = dispatchCountLast24h(runs, now);
  if (recent >= cap) {
    log.watchdog_state = `${decision.state}_cap_exceeded`;
    log.dispatch_count_24h = recent;
    console.log(JSON.stringify(log));
    return log;
  }
  if (dryRun) {
    // 진단 프로브(fetch 핸들러)용 — 여기까지 왔으면 토큰·판정·상한이 전부
    // 정상이고 실제 크론이라면 dispatch 했을 것이라는 뜻.
    log.would_dispatch = true;
    log.dispatch_count_24h = recent;
    return log;
  }

  // trigger_source 는 crawl.yml 의 run-name 이 제목에 찍어 위 상한 계산이 읽는다.
  // ponytail: 복구 lookback 확장(V2 recovery_lookback_hours)은 미이식 — V1
  // LOOKBACK_HOURS=6h 가 슬롯 2개를 덮어 단일 슬롯 누락은 복구된다. 6시간 넘는
  // 장애를 겪으면 crawl.yml 에 입력 추가 + news_bot env 화로 확장할 것.
  await github(env, `${base}/dispatches`, {
    method: "POST",
    body: JSON.stringify({
      ref: "main",
      inputs: { trigger_source: RECOVERY_MARKER, recovery_reason: decision.state },
    }),
  });
  log.dispatched = true;
  console.log(JSON.stringify(log));
  await notify(env, repoBase,
    `[crawl-watchdog] 수집 복구 실행 (${decision.state})\n` +
    `슬롯 ${decision.start.toISOString()} — 원인은 Actions crawl.yml 이력 확인`);
  return log;
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(checkAndRecover(env));
  },
  // GET <workers.dev>/ — 지금 이 순간의 판정을 dry-run 으로 돌려준다. dispatch 는
  // 절대 하지 않는다. 크론이 안 도는 것과 핸들러가 던지는 것을 tail 없이 가르려고
  // 둔 창(2026-09-07 실측: 세 틱 연속 로그 0줄, 원인 미상). 공개 정보(Actions
  // run 상태)만 담긴다.
  async fetch(_request, env) {
    try {
      return Response.json(await checkAndRecover(env, new Date(), { dryRun: true }));
    } catch (error) {
      return Response.json({ error: String(error) }, { status: 500 });
    }
  },
};
