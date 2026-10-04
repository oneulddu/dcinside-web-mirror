(function () {
    "use strict";

    // Retry failed Poker images with backoff and two concurrent retries;
    // keep native lazy loading and the existing image-block controls.
    var states = new WeakMap();
    var queue = [];
    var active = 0;

    function source(image) {
        if (!image || image.tagName !== "IMG") return null;
        var value = image.getAttribute("data-body-image-src");
        return value && value.indexOf("/poker/media?") === 0 ? value : null;
    }

    function eligible(image, url) {
        return image.isConnected && !image.hidden && source(image) === url &&
            image.getAttribute("src") === url && !image.naturalWidth;
    }

    function drain() {
        while (active < 2 && queue.length) {
            var job = queue.shift();
            job.state.queued = false;
            if (!eligible(job.image, job.url)) continue;
            start(job);
        }
    }

    function start(job) {
        var state = job.state;
        active += 1;
        state.attempts += 1;
        // Hiding/navigation or a lost network may never emit load/error.
        var watchdog = setTimeout(function () {
            // Cancel this image's pending load before admitting another retry.
            // Do not cancel a source changed by another control in the meantime.
            if (job.image.getAttribute("src") === job.url && !job.image.naturalWidth) {
                job.image.removeAttribute("src");
            }
            finish();
        }, 35000);
        function finish() {
            if (state.finish !== finish) return;
            clearTimeout(watchdog);
            state.finish = null;
            active -= 1;
            drain();
        }
        state.finish = finish;
        job.image.setAttribute("src", job.url);
    }

    function failed(event) {
        var image = event.target;
        var url = source(image);
        if (!url) return;
        var state = states.get(image);
        if (!state) {
            state = { attempts: 0, queued: false, finish: null };
            states.set(image, state);
        }
        if (state.finish) state.finish();
        if (state.queued || state.attempts >= 2 || !eligible(image, url)) return;
        state.queued = true;
        setTimeout(function () {
            queue.push({ image: image, url: url, state: state });
            drain();
        }, (state.attempts ? 4000 : 1500) + Math.random() * 500);
    }

    // Capture includes comments inserted later. Install before initial srcs.
    document.addEventListener("error", failed, true);
    document.addEventListener("load", function (event) {
        var state = states.get(event.target);
        if (state && state.finish) state.finish();
    }, true);
})();
