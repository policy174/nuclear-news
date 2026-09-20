// 렌더러 계약 — **의미를 만들지 않는다.**
//
// 이 파일이 생긴 이유는 실제 사고 하나다. `pickEditorialArt` 가 그림과 도장을
// 고르면서 세 분기에서 `headline` 까지 시연용 고정 문구로 덮었다(법안 가결,
// 협력 채널 신설, MOU 서명 연기). 걸리는 조건이 `/대미|웨스팅하우스|MOU|한미/`
// 처럼 매우 넓다 — 한미 협력 기사는 거의 매주 나오므로, 그 주제의 카드는 그날
// 무슨 일이 있었든 몇 주 전 문구가 적힌 채 나갈 수 있었다.
//
// 검증과 편집 QA 는 전부 이 앞 단계에 있어서 아무도 못 잡는다 — **검증한
// 문장과 인쇄된 문장이 다른 것**이 문제의 본질이고, 그건 렌더러 쪽에서만
// 잡힌다.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = readFileSync(path.join(here, "..", "build.js"), "utf8");

let passed = 0;
function check(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
    passed += 1;
  } catch (error) {
    console.error(`  ✗ ${name}\n    ${error.message}`);
    process.exitCode = 1;
  }
}

console.log("렌더러는 의미를 바꾸지 않는다");

check("art selector 가 headline 을 받지도 돌려주지도 않는다", () => {
  const signature = source.match(/function pickEditorialArt\(([^)]*)\)/);
  assert.ok(signature, "pickEditorialArt 를 못 찾았다");
  assert.equal(signature[1].trim(), "haystack",
    "headline 을 다시 받기 시작했다 — 덮어쓸 길이 열린다");
  const body = source.slice(source.indexOf("function pickEditorialArt"));
  const ret = body.match(/return \{ image, stamp[^}]*\};/);
  assert.ok(ret, "반환문을 못 찾았다");
  assert.ok(!ret[0].includes("headline"), "반환값에 headline 이 돌아왔다");
});

check("고정 제목 문구가 코드에 남아 있지 않다", () => {
  // 세 분기가 덮어쓰던 문구. 여기서도 글자 그대로 적지 않는다 — 복사해
  // 되살리기 쉬워진다. 조각으로 찾는다.
  for (const ghost of ["비용 부담 법안 [[", "협력 [[채널", "서명 [[연기"]) {
    assert.ok(!source.includes(ghost), `덮어쓰기 문구가 살아 있다: ${ghost}`);
  }
});

check("editorialFromStep 이 headline 을 다시 대입하지 않는다", () => {
  const start = source.indexOf("function editorialFromStep");
  const body = source.slice(start, source.indexOf("\nfunction shell", start));
  // 객체 리터럴에서 `headline,` 또는 `headline:` 으로 값을 넣으면 덮어쓴 것이다.
  // 스프레드(...slide)로 들어오는 원본만 남아야 한다.
  assert.ok(!/^\s*headline[,:]/m.test(body),
    "editorialFromStep 이 headline 을 다시 쓴다");
});

check("덱이 why[0] 을 재사용하지 않는다", () => {
  const start = source.indexOf("function editorialFromStep");
  const body = source.slice(start, source.indexOf("\nfunction shell", start));
  const deck = body.match(/deck:\s*([^\n]*)/);
  assert.ok(deck, "deck 대입을 못 찾았다");
  assert.ok(!deck[1].includes("why["),
    "사실이 둘뿐인 날 why[0] 이 덱과 whyLead 두 자리에 같은 글자로 찍힌다");
});

console.log("\n일일 카드 머리는 한국어만 쓴다");

check("WHAT HAPPENED · WHY IT MATTERS 가 마크업에 없다", () => {
  // 주석에는 남는다(왜 걷었는지를 적어 둔 자리). 마크업 줄에서만 찾는다.
  const markup = source.split("\n")
    .filter((line) => line.includes("editorial-section-head") && line.includes("${"))
    .join("\n");
  assert.ok(markup.length > 0, "구역 머리 마크업을 못 찾았다");
  assert.ok(!markup.includes("WHAT HAPPENED"), "영문 라벨이 살아 있다");
  assert.ok(!markup.includes("WHY IT MATTERS"), "영문 라벨이 살아 있다");
  assert.ok(markup.includes("확인된 사실") && markup.includes("왜 중요한가"),
    "한국어 라벨이 사라졌다");
});

check("빈 문자열로 숨기는 방식이 아니다", () => {
  // `sectionA`/`sectionB` 자체가 사라져야 한다 — 남아 있으면 슬라이드가 값을
  // 넘기는 순간 영문이 되살아난다.
  assert.ok(!/s\.sectionA/.test(source), "sectionA 가 남아 있다");
  assert.ok(!/s\.sectionB/.test(source), "sectionB 가 남아 있다");
  // 라벨 이름도 whyLabel 관례로 통일했다 — "A 는 영문, B 는 한글" 구조가
  // 이름에 남아 있으면 그 구조가 계속 암시된다.
  assert.ok(source.includes("s.factsLabel"), "factsLabel 이 없다");
});

check("스토리의 디자인용 영문 도장은 그대로다", () => {
  assert.ok(source.includes("editorial-stamp"), "도장이 사라졌다");
  assert.ok(source.includes("ENERGY INFRASTRUCTURE"),
    "도장 문구까지 같이 걷어냈다 — 걷을 것은 본문 구역 머리뿐이다");
});

if (process.exitCode) {
  console.error("\n실패");
} else {
  console.log(`\n${passed}건 전부 통과`);
}
