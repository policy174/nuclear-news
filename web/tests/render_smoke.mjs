// 라이브 렌더링 스모크 — API가 정상이어도 app.js 오류로 화면만 깨지는 경우를 잡는다.
// 실행: node web/tests/render_smoke.mjs  (요구: playwright + chromium 설치)
// CI: daily-brief.yml 에서 하루 1회 (크롤 hourly 는 curl 스모크만 — 브라우저 설치 비용 회피)
import { chromium } from "playwright";

const BASE = process.env.SMOKE_URL || "https://nuclens.pages.dev/";
const failures = [];

const meta = await (await fetch(new URL(`data/meta.json?cb=${Date.now()}`, BASE))).json();

const browser = await chromium.launch();
const page = await browser.newPage();

// 실패했을 때 "왜"를 CI 로그에서 바로 읽으려고 모아 둔다. 라이브 검증은 로컬
// 재현이 안 되는 일이 잦아서(러너 환경 전용 실패), 증거를 남기지 않으면
// 다음 세션이 또 처음부터 추측한다.
const inflight = new Map();
const failed = [];
const pageErrors = [];
page.on("request", (request) => inflight.set(request, Date.now()));
page.on("requestfinished", (request) => inflight.delete(request));
page.on("requestfailed", (request) => {
  inflight.delete(request);
  failed.push(`${request.url()} :: ${request.failure()?.errorText || "unknown"}`);
});
page.on("pageerror", (error) => pageErrors.push(String(error).slice(0, 300)));

function diagnostics() {
  const lines = [];
  const now = Date.now();
  for (const [request, started] of inflight) {
    lines.push(`  미완료 요청 (${now - started}ms): ${request.url()}`);
  }
  for (const line of failed) lines.push(`  실패 요청: ${line}`);
  for (const line of pageErrors) lines.push(`  JS 오류: ${line}`);
  return lines;
}

try {
  // waitUntil 로 "networkidle" 을 쓰면 안 된다. 이 앱은 60초 주기로 meta.json 을
  // 폴링하고(checkForNewGeneration), 오디오·폰트·404 부가 요청이 물려 있어
  // 네트워크가 조용해지는 순간이 보장되지 않는다 — 2026-08-04 CI 에서 실제로
  // 60초 타임아웃으로 워크플로가 죽었다(라이브는 멀쩡했다). 로드 완료 신호는
  // 네트워크가 아니라 **렌더러 출력**으로 판정한다.
  await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 60000 });

  // 홈은 목차형(#tocList)이다. index.html 의 #bootSkeleton 이 정적 스켈레톤을
  // 들고 있으므로 그걸 세면 렌더러가 죽어도 잡힌다 — 렌더러가 실제로 돌았다는
  // 신호는 #tocList 안의 .toc-row(동적) 또는 .empty-state 다. #headerStatus 는
  // renderSystemStatus 의 출력 — 둘 다 서면 초기화가 끝난 것.
  try {
    await page.waitForFunction(() => {
      const toc = document.querySelector("#tocList");
      const header = document.querySelector("#headerStatus");
      return !!toc && (toc.querySelector(".toc-row") || toc.querySelector(".empty-state"))
        && !!header && /이슈\s*\d+/.test(header.textContent || "");
    }, null, { timeout: 45000 });
  } catch {
    const headerStatus = (await page.textContent("#headerStatus").catch(() => "")) || "";
    failures.push(`초기 렌더 미완료 (45초) — 헤더 "${headerStatus.trim()}"`);
    for (const line of diagnostics()) failures.push(line.trim());
  }

  const bodyText = (await page.textContent("body")) || "";
  if (/데이터 연결 실패/.test(bodyText)) failures.push("'데이터 연결 실패' 문구가 화면에 있음");

  // 선정 하한 도입 뒤로 이슈 0건은 정상 상태다. "행이 1개 이상"을 요구하면 조용한
  // 날에 CI 가 빨개진다. 대신 렌더러가 무언가를 그렸는가를 본다: 행 아니면 빈 상태.
  const rows = await page.locator("#tocList .toc-row").count();
  const emptyStates = await page.locator("#tocList .empty-state").count();
  if (rows === 0 && emptyStates === 0) failures.push("목차가 행도 빈 상태도 아님 — 렌더 실패 의심");
  if (rows === 0) console.log("주의: 목차 0행 — 빈 상태로 렌더됨(정상 가능)");

  // 행 계약: 번호·분류(또는 지역)·제목 링크·화살표가 다 서야 한다. 하나라도 빠지면
  // 렌더러가 절반만 그린 것이다. 제목은 잘라내지 않는다(clamp 없음) — 넘치면
  // 높이가 늘어야 하고, 잘리면 '승인 중단'이 '승인'이 된다.
  if (rows > 0) {
    const bad = await page.evaluate(() => {
      const out = [];
      for (const row of document.querySelectorAll("#tocList .toc-row")) {
        const miss = [];
        if (!row.querySelector(".toc-num")) miss.push("번호");
        if (!row.querySelector(".toc-region") && !row.querySelector(".chip--domain")) miss.push("분류/지역");
        if (!row.querySelector("a.toc-link[href]")) miss.push("제목 링크");
        const title = row.querySelector(".toc-title");
        if (title && title.scrollHeight > title.clientHeight + 1) miss.push("제목 잘림");
        if (miss.length) out.push(miss.join("·"));
      }
      return out;
    });
    if (bad.length) failures.push(`목차 행 ${bad.length}/${rows}개 계약 위반: ${bad.slice(0, 3).join(" / ")}`);
    // 패널 머리(날짜·건수)가 채워졌는가 — renderBriefing 뒷부분이 돌았다는 신호
    const panelDate = (await page.textContent("#briefPanelDate").catch(() => "")) || "";
    if (!/\d+월 \d+일/.test(panelDate)) failures.push(`브리핑 패널 날짜 미표시 ("${panelDate.trim()}")`);
  }

  // 최신 브리핑 날짜가 화면에 반영됐는가 — "2026-07-31" → "7월 31일" 표기로 확인
  const d = meta.latest_briefing_date || "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(d) && rows > 0) {
    const label = `${parseInt(d.slice(5, 7), 10)}월 ${parseInt(d.slice(8, 10), 10)}일`;
    if (!bodyText.includes(label)) failures.push(`최신 브리핑 날짜(${label}) 미표시`);
  }

  // 발간물 탭 — 데이터가 0건이어도 빈 상태가 렌더돼야 한다(탭 자체가 죽으면 실패).
  // 반드시 #pubsList(=renderPubs 의 출력)를 본다. #view-report 는 index.html 에
  // 정적으로 박힌 h1·안내문을 갖고 있어서, renderPubs 가 통째로 죽어도
  // textContent 가 비지 않는다 — 그걸 검사하면 아무것도 못 잡는다.
  const pubsTab = page.locator('#mainTabs [data-view="report"]');
  if (await pubsTab.count()) {
    await pubsTab.click();
    await page.locator("#view-report").waitFor({ state: "visible", timeout: 10000 })
      .catch(() => failures.push("보고서 탭 클릭 후 #view-report 가 보이지 않음"));
    const listHtml = (await page.innerHTML("#pubsList").catch(() => "")) || "";
    if (!listHtml.trim()) {
      failures.push("renderPubs 가 아무것도 그리지 않음 (#pubsList 비어 있음)");
    }
    // 발간물이 있는 날은 카드가, 없는 날은 빈 상태가 나와야 한다. 둘 다 아니면 렌더 실패다.
    const cards = await page.locator("#pubsList .pub-item").count();
    const empty = await page.locator("#pubsList .empty-state").count();
    if (cards === 0 && empty === 0) {
      failures.push("발간물 목록이 카드도 빈 상태도 아님 — 렌더 실패 의심");
    }
  } else {
    failures.push("발간물 탭 버튼이 없음");
  }

  if (failures.length) for (const line of diagnostics()) failures.push(line.trim());
} catch (error) {
  failures.push(`스모크 실행 중 예외: ${String(error).split("\n")[0]}`);
  for (const line of diagnostics()) failures.push(line.trim());
} finally {
  await browser.close();
}

if (failures.length) {
  console.error("렌더링 스모크 실패:");
  for (const f of failures) console.error(" - " + f);
  process.exit(1);
}
console.log(`렌더링 스모크 OK — ${BASE} (최신 브리핑 ${meta.latest_briefing_date})`);
