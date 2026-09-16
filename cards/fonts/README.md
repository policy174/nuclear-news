# cards/fonts

- 제목 서체는 **SUIT Variable** (SUNN YOUN, OFL 1.1, https://github.com/sunn-us/SUIT).
  `SUIT-Variable.woff2` 610KB. 한글 2,668자뿐이라 **㎾·㎿·㎸ 가 없다**(실측) —
  theme.json 의 heading css 가 Pretendard 를 뒤에 세워 글자별로 받친다. 이 폴백을 빼면
  '345㎸ 송전선' 같은 줄이 시스템 폰트로 새어 나간다.
- 본문 서체는 **Pretendard Variable** — 사이트가 든 `web/public/fonts/pretendard/v1.3.9/PretendardVariable.woff2`(원본, 서브셋 아님)를 build.js 가 임베딩한다. 별도 파일 없음.
- `WantedSansVariable.woff2`(미사용, 09-17 "아저씨 글씨체" 판정) — Wanted Sans (원티드랩), SIL Open Font License 1.1.
  출처: https://github.com/wanteddev/wanted-sans
  build.js 가 base64 로 임베딩한다 — 외부 CDN 의존 0. 이 파일이 없으면
  렌더 가드가 font=false 로 죽인다(조용히 폴백 폰트로 나가지 않는다).
