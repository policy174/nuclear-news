// D1 시안 — 토글과 '읽은 항목' 표시만. 화면 로직은 여기 없다.
//
// 뒤로 가기 스크롤 복원은 **코드가 없다** — 행이 진짜 <a href> 라
// 브라우저가 알아서 한다. SPA pushState 로 만들었으면 직접 짜야 했을 것이다.
// D2 에서 이게 실제로 되는지 확인한다.

const KEY = "mk-read";
const read = new Set(JSON.parse(localStorage.getItem(KEY) || "[]"));

document.querySelectorAll(".toc-row").forEach((row) => {
  if (read.has(row.dataset.id)) row.classList.add("is-read");
  row.addEventListener("click", () => {
    read.add(row.dataset.id);
    localStorage.setItem(KEY, JSON.stringify([...read]));
  });
});

const bind = (id, cls) => {
  const box = document.getElementById(id);
  if (!box) return;
  const saved = localStorage.getItem(id) === "1";
  box.checked = saved;
  document.body.classList.toggle(cls, saved);
  box.addEventListener("change", () => {
    document.body.classList.toggle(cls, box.checked);
    localStorage.setItem(id, box.checked ? "1" : "0");
  });
};
bind("tgTint", "tint");
bind("tgCompact", "compact");
