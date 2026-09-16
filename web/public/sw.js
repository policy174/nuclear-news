// 서비스워커 — 푸시 알림 전용. 캐시는 하지 않는다: 이 사이트는 매시 데이터가 바뀌고
// 브라우저 캐시 때문에 옛 화면을 보는 사고가 이미 있었다(2026-09-15 style.css).
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data && event.data.text() }; }
  const title = data.title || "Nuclens 오늘 브리핑";
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || "오늘의 원전 현안이 올라왔습니다.",
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    tag: data.tag || "nuclens-brief",
    renotify: false,
    data: { url: data.url || "/?src=push" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((wins) => {
    const same = wins.find((w) => w.url.startsWith(self.location.origin));
    if (same) return same.focus().then((w) => (w && "navigate" in w ? w.navigate(url) : w));
    return self.clients.openWindow(url);
  }));
});
