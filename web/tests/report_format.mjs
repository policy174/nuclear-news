// 보고서용 복사의 표기 검사 — 사내 서식(khnp-report style-rules §2)과 각주 번호.
// 실행: node web/tests/report_format.mjs  (의존성 없음)
//
// date_window.mjs 와 같은 방식이다: app.js 는 모듈이 아니라 최상위에서 DOM 을
// 건드리는 스크립트라 import 가 안 된다. 함수 블록만 잘라 평가한다.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const source = readFileSync(
  fileURLToPath(new URL("../public/app.js", import.meta.url)), "utf8");

function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  if (start < 0) throw new Error(`app.js 에 ${name}() 이 없다 — 이름이 바뀌었으면 이 검사도 같이 고쳐라`);
  let depth = 0;
  for (let i = source.indexOf("{", start); i < source.length; i += 1) {
    if (source[i] === "{") depth += 1;
    else if (source[i] === "}" && (depth -= 1) === 0) return source.slice(start, i + 1);
  }
  throw new Error(`${name}() 블록이 안 닫힌다`);
}

// footnoteBook 은 sourceLabel·safeUrl 을 쓴다 — 표기 검사에 필요한 최소 구현만
// 세워 준다. 진짜 구현의 계약(매체명 폴백, 위험 URL 차단)은 각자의 자리에서 본다.
const { reportDate, footnoteBook } = new Function(`
  const sourceLabel = a => a.publisher || a.domain || "출처 미상";
  const safeUrl = u => (typeof u === "string" && /^https?:/.test(u) ? u : "");
  ${extract("reportDate")}
  ${extract("footnoteBook")}
  return { reportDate, footnoteBook };
`)();

const cases = [];
const eq = (label, got, want) => {
  cases.push({ label, got: JSON.stringify(got), want: JSON.stringify(want),
               ok: JSON.stringify(got) === JSON.stringify(want) });
};

// 연도는 아포스트로피 두 자리, 월·일은 앞 0 을 뗀다.
eq("'26.8.14. 꼴", reportDate("2026-08-14"), "'26.8.14.");
eq("두 자리 월·일도 0 없이", reportDate("2026-12-31"), "'26.12.31.");
eq("타임스탬프도 날짜만", reportDate("2026-08-14T09:30:00+09:00"), "'26.8.14.");
eq("빈 값은 빈 문자열", reportDate(""), "");
eq("형식이 다르면 빈 문자열", reportDate("2026/08/14"), "");

// 각주는 같은 기사에 같은 번호를 준다 — 본문에서 두 번 인용해도 목록은 한 줄.
const notes = footnoteBook();
const a = { publisher: "Reuters", title_kr: "가", url: "https://x.test/1", article_date: "2026-08-14" };
const b = { publisher: "WNN", title_kr: "나", url: "https://x.test/2", article_date: "2026-08-13" };
eq("첫 인용은 [1]", notes.cite(a), " [1]");
eq("다른 기사는 [2]", notes.cite(b), " [2]");
eq("같은 기사 재인용은 [1] 유지", notes.cite(a), " [1]");
// 빈 줄 + "*출처 :" + 각주 2행.
eq("목록 줄 수", notes.lines().length, 4);
eq("각주 머리말", notes.lines()[1], "*출처 :");
eq("첫 각주 형식", notes.lines()[2], "[1] Reuters, 「가」, '26.8.14., https://x.test/1");

// 붙일 게 없으면 번호를 만들지 않는다 — 빈 [n] 이 본문에 남으면 각주가 거짓말이 된다.
const empty = footnoteBook();
eq("빈 기사에는 각주 없음", empty.cite({}), "");
eq("빈 기사만이면 목록도 없음", empty.lines(), []);

// 위험한 URL 은 safeUrl 이 걷는다. 제목이 있으면 각주는 남되 링크는 안 붙는다.
const risky = footnoteBook();
eq("javascript: 는 링크 없이", risky.cite({ publisher: "X", title_kr: "다", url: "javascript:alert(1)" }),
   " [1]");
eq("각주에 스킴 유출 없음", /javascript:/.test(risky.lines().join("\n")), false);

const failed = cases.filter(row => !row.ok);
for (const row of cases) {
  console.log(`${row.ok ? "ok  " : "FAIL"} ${row.label} → ${row.got}${row.ok ? "" : ` (기대 ${row.want})`}`);
}
if (failed.length) {
  console.error(`\n${failed.length}/${cases.length} 실패`);
  process.exit(1);
}
console.log(`\n${cases.length}건 전부 통과`);
