"use strict";

// 댓글·답글 푸시 알림 워커. 받은 내용만으로 바로 알림을 띄우고 네트워크 요청은 하지 않는다.
const ICON = "/static/icons/icon-192.png";
const FALLBACK = { title: "숨터", body: "새 댓글이나 답글이 있어요.", tag: "mirror:fallback" };
const text = value => typeof value === "string" ? value : "";
// reply_inbox.js의 follow()와 같은 이름을 쓴다.
const HANDOFF = "mirror-notification-open", HANDOFF_KEY = "/__mirror-notification-open";

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
    event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(async windows => {
        const open = windows.find(client => client.url === url);
        if (open) return open.focus();
        if (!windows.length) return self.clients.openWindow(url);
        // iOS 홈 화면 앱이 열려 있으면 openWindow는 앱만 앞으로 가져오고 주소는 그대로 둔다.
        // 화면이 워커 범위 밖이라 navigate()도 못 쓰므로, 주소를 남겨 두고 열린 화면이 직접 이동한다.
        const cache = await self.caches.open(HANDOFF);
        await cache.put(HANDOFF_KEY, new Response(JSON.stringify({ url, at: Date.now() })));
        windows.forEach(client => client.postMessage(HANDOFF));
        return windows[0].focus().catch(() => { /* 포커스를 못 줘도 화면이 보이면 주소를 가져간다. */ });
    }));
});
