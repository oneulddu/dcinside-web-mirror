(function (root) {
    "use strict";

    // 글 보기에서 이미 불러온 댓글만 거른다. 디시에는 요청하지 않는다.
    function normalize(value) {
        return String(value == null ? "" : value).toLocaleLowerCase("ko-KR").replace(/\s+/g, " ").trim();
    }

    // 댓글 본문과 작성자 이름·식별 코드만 찾는다. 버튼이나 입력칸 글자는 넣지 않는다.
    function searchText(item) {
        var body = item.querySelector(".comment-main p");
        var author = item.querySelector(".comment-meta .author-text");
        var parts = [body ? body.textContent : ""];
        if (author) {
            parts.push(author.getAttribute("data-author") || "", author.getAttribute("data-author-code") || "");
        }
        return normalize(parts.join(" "));
    }

    function matches(text, query) {
        return !query || text.indexOf(query) >= 0;
    }

    function statusText(query, count, partial) {
        if (!query) {
            return "";
        }
        var text = count ? count.toLocaleString("ko-KR") + "개 찾았어요." : "찾는 댓글이 없어요.";
        return partial ? text + " 일부만 불러온 댓글 안에서 찾았어요." : text;
    }

    root.MirrorCommentSearch = { normalize: normalize, matches: matches, statusText: statusText };

    var document = root.document;
    if (!document) {
        return;
    }
    var input = document.getElementById("comment-search-input");
    var list = document.getElementById("comment-list");
    var status = document.getElementById("comment-search-status");
    if (!input || !list || !status) {
        return;
    }
    var partial = input.getAttribute("data-partial") === "true";

    // 차단·도배 필터가 숨긴 댓글은 찾은 개수에 넣지 않는다.
    function hiddenByOthers(item) {
        return item.classList.contains("comment-spam-hidden") || item.classList.contains("is-user-filtered");
    }

    function apply() {
        var query = normalize(input.value);
        var count = 0;
        Array.prototype.forEach.call(list.querySelectorAll(":scope > .comment-item"), function (item) {
            var hit = matches(searchText(item), query);
            item.classList.toggle("is-search-hidden", !hit);
            if (query && hit && !hiddenByOthers(item)) {
                count += 1;
            }
        });
        list.classList.toggle("is-searching", Boolean(query));
        status.textContent = statusText(query, count, partial);
    }

    input.addEventListener("input", apply);
    input.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && input.value) {
            event.preventDefault();
            input.value = "";
            apply();
        }
    });
    // 댓글을 달거나 지우면 찾는 중인 결과를 다시 맞춘다. 클래스만 바꾸므로 다시 불리지 않는다.
    if (typeof root.MutationObserver === "function") {
        new root.MutationObserver(function () {
            if (input.value) {
                apply();
            }
        }).observe(list, { childList: true, subtree: true, characterData: true });
    }
})(typeof window !== "undefined" ? window : globalThis);
