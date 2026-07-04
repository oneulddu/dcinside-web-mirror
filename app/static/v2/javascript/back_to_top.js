(function () {
    "use strict";

    var SHOW_AFTER_PX = 480;

    function boot() {
        var button = document.getElementById("back-to-top");
        if (!button) {
            return;
        }

        function sync() {
            button.classList.toggle("is-visible", window.scrollY > SHOW_AFTER_PX);
        }

        button.addEventListener("click", function () {
            var reduceMotion = false;
            try {
                reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            } catch (err) {
                reduceMotion = false;
            }
            window.scrollTo({ top: 0, behavior: reduceMotion ? "auto" : "smooth" });
        });

        window.addEventListener("scroll", sync, { passive: true });
        sync();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot, { once: true });
    } else {
        boot();
    }
})();
