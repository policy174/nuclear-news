/**
 * 본문 폴백 — 봇 차단을 실제 브라우저로 넘는다.
 *
 * iaea.org 는 Cloudflare 판정이라 헤더로는 못 푼다(실측 2026-09-19: UA·Accept·
 * Sec-Fetch·Accept-Encoding 조합을 바꿔도 전부 403, 처음 두 번만 통과했다).
 * 카드 렌더가 이미 puppeteer 를 쓰고 있으므로, 실패한 기사만 여기로 보낸다.
 *
 *   node fetch_body.js '["https://...","https://..."]'   → {"url": "본문", ...}
 *
 * 본문은 호출자가 그 실행 안에서만 쓰고 저장하지 않는다(article_body 의 계약).
 */
const puppeteer = require("puppeteer");

const NAV_TIMEOUT = 25000;
const MAX_CHARS = 8000;
const UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

async function main() {
  const urls = JSON.parse(process.argv[2] || "[]").slice(0, 8);
  const out = {};
  if (!urls.length) {
    process.stdout.write("{}");
    return;
  }
  const browser = await puppeteer.launch({ headless: "new", args: ["--no-sandbox"] });
  try {
    const page = await browser.newPage();
    await page.setUserAgent(UA);
    await page.setExtraHTTPHeaders({ "Accept-Language": "ko,en-US;q=0.8,en;q=0.6" });
    // 이미지·폰트·미디어는 본문과 무관하다 — 막으면 한 건이 3초대로 떨어진다.
    await page.setRequestInterception(true);
    page.on("request", (req) => {
      const type = req.resourceType();
      if (type === "image" || type === "font" || type === "media") req.abort();
      else req.continue();
    });
    for (const url of urls) {
      try {
        await page.goto(url, { waitUntil: "domcontentloaded", timeout: NAV_TIMEOUT });
        const text = await page.evaluate(() => {
          const drop = "script,style,noscript,nav,aside,header,footer,form,figure";
          const scope =
            document.querySelector("article") ||
            document.querySelector("main") ||
            document.body;
          if (!scope) return "";
          const clone = scope.cloneNode(true);
          clone.querySelectorAll(drop).forEach((el) => el.remove());
          // 메뉴·버튼 같은 짧은 줄은 버린다 — innerText 는 내비게이션까지 다 담는다.
          return (clone.innerText || "")
            .split("\n")
            .map((line) => line.trim())
            .filter((line) => line.length >= 25)
            .join("\n")
            .trim();
        });
        out[url] = String(text || "").slice(0, MAX_CHARS);
      } catch (e) {
        out[url] = "";   // 한 건이 실패해도 나머지는 받는다
      }
    }
  } finally {
    await browser.close();
  }
  process.stdout.write(JSON.stringify(out));
}

main().catch((e) => {
  process.stderr.write(String((e && e.message) || e));
  process.stdout.write("{}");
});
