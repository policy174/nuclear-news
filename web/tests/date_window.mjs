// 집계 창 산술 검사 — '최근 7일'이 8일이 되는 off-by-one 재발 방지.
// 실행: node web/tests/date_window.mjs  (의존성 없음)
//
// app.js 는 모듈이 아니라 최상위에서 DOM 을 건드리는 평범한 스크립트라 import 가
// 안 된다. 그래서 소스에서 해당 함수 블록만 잘라 평가한다 — 함수 이름이 바뀌면
// 조용히 통과하지 않고 여기서 먼저 깨지도록 이름으로 찾는다.
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

const { shiftDate, weekRange, trendStart, timeLabel } = new Function(`
  ${extract("shiftDate")}
  ${extract("weekRange")}
  ${extract("todayKST")}
  ${extract("dateTimeLabel")}
  ${extract("timeLabel")}
  // trendRange() 는 state/meta 에 얽혀 있어 창 계산부만 같은 헬퍼로 재현한다.
  const trendStart = (end, days) => shiftDate(end, -(days - 1));
  return { shiftDate, weekRange, trendStart, timeLabel };
`)();

const cases = [];
const eq = (label, got, want) => cases.push({ label, got, want, ok: got === want });

// 핵심 회귀: KST 자정을 UTC 로 되돌리며 하루가 더 빠지던 지점.
eq("weekRange 08-09 시작", weekRange("2026-08-09").start, "2026-08-03");
eq("weekRange 08-09 끝", weekRange("2026-08-09").end, "2026-08-09");
eq("주간 창 길이 = 7일", 1 + (Date.parse("2026-08-09") - Date.parse(weekRange("2026-08-09").start)) / 86400000, 7);

// 월·연 경계에서도 문자열 산술이 성립하는가.
eq("월 경계", weekRange("2026-03-02").start, "2026-02-24");
eq("윤년 2월", shiftDate("2028-03-01", -1), "2028-02-29");
eq("연 경계", shiftDate("2026-01-01", -1), "2025-12-31");

// 트렌드 토글: 7일은 7일, 30일은 30일.
eq("trend 7일 창", trendStart("2026-08-10", 7), "2026-08-04");
eq("trend 30일 창", trendStart("2026-08-10", 30), "2026-07-12");

// 빈 값이 들어와도 RangeError 로 렌더를 죽이지 않는다(옛 코드는 throw 했다).
eq("빈 날짜는 빈 문자열", shiftDate("", -6), "");

// 낡은 수집 시각이 오늘로 읽히지 않는가 — 시:분만 찍던 자리.
// 오늘 것은 시:분만, 어제 이전 것은 날짜를 달고 나와야 한다.
const nowKST = timeLabel(new Date().toISOString());
const oldStamp = timeLabel("2026-08-09T06:54:30+09:00");
eq("오늘 시각은 HH:MM", /^\d{2}:\d{2}$/.test(nowKST), true);
eq("지난 시각은 날짜 동반", /^\d{2}:\d{2}$/.test(oldStamp), false);
eq("지난 시각에 월·일 포함", /8[.\s]*\s*9/.test(oldStamp), true);

const failed = cases.filter(row => !row.ok);
for (const row of cases) {
  console.log(`${row.ok ? "ok  " : "FAIL"} ${row.label} → ${row.got}${row.ok ? "" : ` (기대 ${row.want})`}`);
}
if (failed.length) {
  console.error(`\n${failed.length}/${cases.length} 실패`);
  process.exit(1);
}
console.log(`\n${cases.length}건 전부 통과`);
