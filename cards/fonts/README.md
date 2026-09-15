# cards/fonts

- `WantedSansVariable.woff2` — Wanted Sans (원티드랩), SIL Open Font License 1.1.
  출처: https://github.com/wanteddev/wanted-sans
  build.js 가 base64 로 임베딩한다 — 외부 CDN 의존 0. 이 파일이 없으면
  렌더 가드가 font=false 로 죽인다(조용히 폴백 폰트로 나가지 않는다).
