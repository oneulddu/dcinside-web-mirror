"use strict";

// 댓글·답글 푸시 알림 워커. 받은 내용만으로 바로 알림을 띄우고 네트워크 요청은 하지 않는다.
const ICON = "/static/icons/icon-192.png";
const FALLBACK = { title: "숨터", body: "새 댓글이나 답글이 있어요.", tag: "mirror:fallback" };
const text = value => typeof value === "string" ? value : "";

// 알림에서 여는 곳은 같은 사이트의 홈과 글 화면뿐이다. 그 밖의 주소는 홈으로 바꾼다.
function allowed(path) {
    return typeof path === "string" && (path === "/" || path.startsWith("/read?")) ? path : "/";
}

self.addEventListener("push", event => {
    let payload = null;
    try { payload = event.data ? event.data.json() : null; } catch (_) { /* 읽을 수 없는 내용이면 기본 알림을 띄운다. */ }
    const usable = Boolean(text(payload?.title));
    const message = usable ? payload : FALLBACK;
    event.waitUntil(self.registration.showNotification(message.title, {
        body: text(message.body), tag: text(message.tag) || FALLBACK.tag, renotify: true, icon: ICON,
        data: { url: allowed(usable ? payload.url : "/") }
    }));
});

self.addEventListener("notificationclick", event => {
    event.notification.close();
    const url = new URL(allowed(event.notification.data?.url), self.location.origin).href;
    event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(windows => {
        const open = windows.find(client => client.url === url);
        return open ? open.focus() : self.clients.openWindow(url);
    }));
});
