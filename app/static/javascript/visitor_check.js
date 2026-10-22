(function () {
    "use strict";

    var message = document.getElementById("visitor-check-message");
    if (!message) {
        return;
    }
    function stop() {
        message.textContent = "방문 확인을 마치지 못했어요. 이 사이트의 JavaScript와 쿠키·저장소를 허용한 뒤 새 탭에서 다시 열어 주세요.";
    }

    try {
        var data = message.dataset;
        var key = "mirror_visitor_attempts";
        var attempts = Number(sessionStorage.getItem(key) || 0);
        if (!Number.isInteger(attempts) || attempts < 0 || attempts >= 2) {
            stop();
            return;
        }
        // Keep the counter across challenge loads even if the server rejects the cookie.
        sessionStorage.setItem(key, String(attempts + 1));
        document.cookie = data.cookie + "=" + data.token + "; Path=/; Max-Age=" + data.maxAge
            + "; SameSite=Lax" + (data.secure === "1" ? "; Secure" : "");
        var stored = document.cookie.split(";").some(function (part) {
            var pair = part.trim();
            var equals = pair.indexOf("=");
            return pair.slice(0, equals) === data.cookie && pair.slice(equals + 1) === data.token;
        });
        if (!stored) {
            stop();
            return;
        }
        // Reload also fetches URLs with #comments; assigning the same hash URL may not.
        location.reload();
    } catch (err) {
        stop();
    }
})();
