(function (root) {
    "use strict";

    // 상단 작성자 메뉴. 고른 값은 글쓰기·댓글·추천이 함께 쓴다. 로그인은 여기서 하지 않는다.
    var document = root.document;
    var post = root.MirrorDcPost;
    if (!document || !post || typeof post.authorMode !== "function") {
        return;
    }
    var control = document.querySelector(".author-pick-control");
    if (!control) {
        return;
    }
    var button = control.querySelector(".author-pick-toggle");
    var menu = control.querySelector(".author-pick-menu");
    var badge = control.querySelector(".author-pick-badge");
    var options = Array.prototype.slice.call(menu.querySelectorAll("[data-author-mode]"));
    // 유동과 계정 하나뿐이면 번호 대신 "디시 계정"이라고 부른다.
    var single = options.length === 2;
    var names = {};

    function render() {
        var mode = post.authorMode();
        var slot = post.modeAccount(mode);
        var label = mode ? post.authorLabel(mode, names, single) : "다시 골라 주세요";
        var text = "작성자: " + label + " (글쓰기·댓글·추천)";
        button.setAttribute("aria-label", text);
        button.title = text;
        button.classList.toggle("is-account", Boolean(slot));
        button.classList.toggle("is-stale", !mode);
        badge.textContent = !mode ? "!" : (slot && !single ? slot : "");
        options.forEach(function (option) {
            var value = option.getAttribute("data-author-mode");
            var selected = value === mode;
            option.setAttribute("aria-checked", selected ? "true" : "false");
            option.classList.toggle("is-selected", selected);
            var name = option.querySelector(".media-block-option-name");
            if (name && post.modeAccount(value)) {
                name.textContent = post.authorLabel(value, names, single);
            }
        });
    }

    function items() {
        return Array.prototype.slice.call(menu.querySelectorAll(".media-block-option"));
    }

    function close(focus) {
        menu.hidden = true;
        button.setAttribute("aria-expanded", "false");
        if (focus) {
            button.focus();
        }
    }

    function open() {
        // 닉네임은 메뉴를 열 때마다 다시 묻는다. 다른 탭에서 로그인했을 수 있다(서버 메모리 값만 읽고 디시에는 묻지 않는다).
        if (typeof post.authorNames === "function") {
            post.authorNames(true).then(function (result) {
                names = result || {};
                render();
            });
        }
        menu.hidden = false;
        button.setAttribute("aria-expanded", "true");
        // 버튼 오른쪽에 다른 버튼이 있어 좁은 화면에서는 메뉴가 왼쪽 밖으로 나간다. 화면 안으로 민다.
        menu.style.right = "";
        var rect = menu.getBoundingClientRect();
        var margin = Math.max(0, Math.min(16, (root.innerWidth - rect.width) / 2));
        if (rect.left < margin) {
            menu.style.right = (parseFloat(root.getComputedStyle(menu).right) || 0) - (margin - rect.left) + "px";
        }
        var selected = menu.querySelector('[aria-checked="true"]') || items()[0];
        if (selected) {
            selected.focus();
        }
    }

    button.addEventListener("click", function () {
        if (menu.hidden) {
            open();
        } else {
            close(false);
        }
    });
    menu.addEventListener("click", function (event) {
        var option = event.target.closest("[data-author-mode]");
        if (!option) {
            return;
        }
        post.setAuthorMode(option.getAttribute("data-author-mode"));
        close(true);
    });
    menu.addEventListener("keydown", function (event) {
        if (["ArrowDown", "ArrowUp", "Home", "End"].indexOf(event.key) < 0) {
            return;
        }
        var list = items();
        event.preventDefault();
        var index = list.indexOf(document.activeElement);
        if (event.key === "Home") {
            index = 0;
        } else if (event.key === "End") {
            index = list.length - 1;
        } else {
            index = (index + (event.key === "ArrowDown" ? 1 : -1) + list.length) % list.length;
        }
        list[index].focus();
    });
    document.addEventListener("click", function (event) {
        if (!control.contains(event.target)) {
            close(false);
        }
    });
    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && !menu.hidden) {
            close(true);
        }
    });
    post.onAuthorChange(render);
    render();
})(typeof window !== "undefined" ? window : globalThis);
