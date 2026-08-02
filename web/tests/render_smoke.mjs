// 라이브 렌더링 스모크 — API가 정상이어도 app.js 오류로 화면만 깨지는 경우를 잡는다.
// 실행: node web/tests/render_smoke.mjs  (요구: playwright + chromium 설치)
// CI: daily-brief.yml 에서 하루 1회 (크롤 hourly 는 curl 스모크만 — 브라우저 설치 비용 회피)
import { chromium } from "playwright";

const BASE = process.env.SMOKE_URL || "https://nuclens.pages.dev/";
const failures = [];

const meta = await (await fetch(new URL(`data/meta.json?cb=${Date.now()}`, BASE))).json();

const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(BASE, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(3000);

  const bodyText = (await page.textContent("body")) || "";
  const metaLine = (await page.textContent("#metaLine").catch(() => "")) || "";
  const articles = await page.locator("article").count();

  if (/데이터 연결 실패/.test(bodyText)) failures.push("'데이터 연결 실패' 문구가 화면에 있음");
  if (articles < 1) failures.push(`이슈 카드 0개 (article 요소 ${articles})`);
  if (!/이슈\s*\d+/.test(metaLine)) failures.push(`메타 라인 비정상: "${metaLine}"`);

  // 최신 브리핑 날짜가 화면에 반영됐는가 — "2026-07-31" → "7월 31일" 표기로 확인
  const d = meta.latest_briefing_date || "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(d)) {
    const label = `${parseInt(d.slice(5, 7), 10)}월 ${parseInt(d.slice(8, 10), 10)}일`;
    if (!bodyText.includes(label)) failures.push(`최신 브리핑 날짜(${label}) 미표시`);
  }

  // 발간물 탭 — 데이터가 0건이어도 빈 상태가 렌더돼야 한다(탭 자체가 죽으면 실패).
  // 반드시 #pubsList(=renderPubs 의 출력)를 본다. #view-pubs 는 index.html 에
  // 정적으로 박힌 h1·안내문을 갖고 있어서, renderPubs 가 통째로 죽어도
  // textContent 가 비지 않는다 — 그걸 검사하면 아무것도 못 잡는다.
  const pubsTab = page.locator('#mainTabs [data-view="pubs"]');
  if (await pubsTab.count()) {
    await pubsTab.click();
    await page.waitForTimeout(800);
    if (!(await page.locator("#view-pubs").isVisible())) {
      failures.push("발간물 탭 클릭 후 #view-pubs 가 보이지 않음");
    }
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
} finally {
  await browser.close();
}

if (failures.length) {
  console.error("렌더링 스모크 실패:");
  for (const f of failures) console.error(" - " + f);
  process.exit(1);
}
console.log(`렌더링 스모크 OK — ${BASE} (최신 브리핑 ${meta.latest_briefing_date})`);
