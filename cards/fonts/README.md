# cards/fonts

- 카드 기본 서체는 **Pretendard Variable** — 사이트가 든 `web/public/fonts/pretendard/v1.3.9/PretendardVariable.woff2`(원본, 서브셋 아님)를 build.js 가 임베딩한다. 별도 파일 없음.
- `WantedSansVariable.woff2`(미사용, 09-17 "아저씨 글씨체" 판정) — Wanted Sans (원티드랩), SIL Open Font License 1.1.
  출처: https://github.com/wanteddev/wanted-sans
  build.js 가 base64 로 임베딩한다 — 외부 CDN 의존 0. 이 파일이 없으면
  렌더 가드가 font=false 로 죽인다(조용히 폴백 폰트로 나가지 않는다).
