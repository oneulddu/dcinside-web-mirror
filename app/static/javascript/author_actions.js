(function () {
    "use strict";

    // 글 보기의 작성자·댓글 작성자 이름을 버튼으로 바꾸고, 누르면
    // "이 사용자 글 보기 / 메모 / 차단 설정" 메뉴를 연다. 목록 행은 전체가 링크라 버튼을 넣지 않는다.
    var AUTHOR_SPAN_SELECTOR = ".article-meta span.author-text[data-author], .comment-meta span.author-text[data-author]";
    var GUEST_CODE = /^\d{1,3}\.\d{1,3}$/;
    var menu = null;
    var currentButton = null;

    function enhance(scope) {
        var spans = (scope || document).querySelectorAll(AUTHOR_SPAN_SELECTOR);
        Array.prototype.forEach.call(spans, function (span) {
            if (span.closest("a")) {
                return;
            }
            var button = document.createElement("button");
            button.type = "button";
            button.className = span.className + " author-action-btn";
            ["data-author", "data-author-code", "data-author-search-name"].forEach(function (name) {
                if (span.hasAttribute(name)) {
                    button.setAttribute(name, span.getAttribute(name));
                }
            });
            button.setAttribute("aria-haspopup", "menu");
            button.setAttribute("aria-expanded", "false");
            button.setAttribute("aria-label", (span.getAttribute("data-author") || "작성자") + " 작성자 메뉴");
            button.textContent = span.textContent;
            span.replaceWith(button);
        });
    }

    function boardContext() {
        // 공지 글은 다른 게시글 목록이 없으므로 본문 영역에 둔 게시판 정보를 먼저 읽는다.
        var section = document.querySelector("[data-board-context]") || document.getElementById("related-section");
        if (!section) {
            return null;
        }
        return {
            board: section.getAttribute("data-board") || "",
            kind: section.getAttribute("data-kind") || "",
            galleryName: section.getAttribute("data-gallery-name") || ""
        };
    }

    function searchHref(name) {
        var context = boardContext();
        if (!context || !context.board || !name) {
            return null;
        }
        var params = new URLSearchParams();
        params.set("board", context.board);
        params.set("page", "1");
        params.set("recommend", "0");
        params.set("s_type", "name");
        params.set("serval", name);
        if (context.kind) {
            params.set("kind", context.kind);
        }
        if (context.galleryName) {
            params.set("gallery_name", context.galleryName);
        }
        return "/board?" + params.toString();
    }

    function option(tag, name, desc) {
        var item = document.createElement(tag);
        item.className = "media-block-option author-menu-item";
        item.setAttribute("role", "menuitem");
        var label = document.createElement("span");
        label.className = "media-block-option-name";
        label.textContent = name;
        var detail = document.createElement("span");
        detail.className = "media-block-option-desc";
        detail.textContent = desc;
        item.appendChild(label);
        item.appendChild(detail);
        if (tag === "button") {
            item.type = "button";
        }
        return item;
    }

    function disable(item) {
        item.setAttribute("aria-disabled", "true");
        item.classList.add("is-disabled");
        if (item.tagName === "A") {
            item.removeAttribute("href");
        }
    }

    function buildMenu(button) {
        var author = button.getAttribute("data-author") || "작성자";
        var code = button.getAttribute("data-author-code") || "";
        var searchName = button.getAttribute("data-author-search-name") || "";
        var guest = GUEST_CODE.test(code);

        var root = document.createElement("div");
        root.className = "author-menu";
        root.id = "author-menu";
        root.setAttribute("role", "menu");
        root.setAttribute("aria-label", author + " 작성자 메뉴");

        var title = document.createElement("div");
        title.className = "media-block-menu-title";
        title.textContent = author + (code ? "(" + code + ")" : "");
        root.appendChild(title);

        var href = searchHref(searchName);
        var search = option(href ? "a" : "div", "이 사용자 글 보기",
            !href ? "원본 닉네임을 알 수 없어 검색할 수 없어요."
                : guest ? "‘" + searchName + "’ 닉네임 검색이라 IP로 구분되지 않아요."
                    : "‘" + searchName + "’ 닉네임으로 이 게시판을 검색해요.");
        if (href) {
            search.href = href;
        } else {
            disable(search);
        }
        root.appendChild(search);

        var memoApi = window.MirrorUserMemo;
        var canMemo = !!(memoApi && memoApi.canMemo && memoApi.canMemo(button));
        var hasMemo = canMemo && !!memoApi.memoFor(button);
        var memo = option("button", hasMemo ? "메모 고치기" : "메모 남기기",
            canMemo ? "이 브라우저에만 저장돼요." : "식별 코드와 닉네임이 없어 메모를 남길 수 없어요.");
        memo.setAttribute("data-author-action", "memo");
        if (!canMemo) {
            disable(memo);
        }
        root.appendChild(memo);

        var filterApi = window.MirrorUserFilter;
        var block = option("button", "차단 설정", code ? "식별 코드로 글과 댓글을 가려요." : "닉네임으로 글과 댓글을 가려요.");
        block.setAttribute("data-author-action", "block");
        if (!filterApi || !filterApi.openForAuthor) {
            disable(block);
        }
        root.appendChild(block);
        return root;
    }

    function enabledItems() {
        return menu ? Array.prototype.filter.call(menu.querySelectorAll(".author-menu-item"), function (item) {
            return item.getAttribute("aria-disabled") !== "true";
        }) : [];
    }

    function place(button) {
        var rect = button.getBoundingClientRect();
        var width = menu.offsetWidth;
        var margin = 12;
        var left = Math.min(Math.max(rect.left, margin), window.innerWidth - width - margin);
        var top = rect.bottom + 6;
        var height = menu.offsetHeight;
        if (top + height > window.innerHeight - margin && rect.top - height - 6 > margin) {
            top = rect.top - height - 6;
        }
        menu.style.left = Math.max(left, margin) + window.scrollX + "px";
        menu.style.top = top + window.scrollY + "px";
    }

    function closeMenu(focusButton) {
        if (!menu) {
            return;
        }
        menu.remove();
        menu = null;
        var button = currentButton;
        currentButton = null;
        if (button) {
            button.setAttribute("aria-expanded", "false");
            button.removeAttribute("aria-controls");
            if (focusButton) {
                button.focus();
            }
        }
    }

    function openMenu(button) {
        closeMenu(false);
        currentButton = button;
        menu = buildMenu(button);
        document.body.appendChild(menu);
        place(button);
        button.setAttribute("aria-expanded", "true");
        button.setAttribute("aria-controls", "author-menu");
        var first = enabledItems()[0];
        if (first) {
            first.focus();
        }
        menu.addEventListener("click", function (event) {
            var item = event.target.closest(".author-menu-item");
            if (!item || item.getAttribute("aria-disabled") === "true") {
                event.preventDefault();
                return;
            }
            var action = item.getAttribute("data-author-action");
            if (!action) {
                closeMenu(false);
                return;
            }
            var target = currentButton;
            closeMenu(false);
            if (action === "memo" && window.MirrorUserMemo) {
                window.MirrorUserMemo.openEditor(target);
            } else if (action === "block" && window.MirrorUserFilter) {
                window.MirrorUserFilter.openForAuthor(target);
            }
        });
        menu.addEventListener("keydown", function (event) {
            var items = enabledItems();
            if (event.key === "Escape") {
                event.preventDefault();
                closeMenu(true);
                return;
            }
            if (event.key === "Tab") {
                closeMenu(false);
                return;
            }
            if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) || !items.length) {
                return;
            }
            event.preventDefault();
            var index = items.indexOf(document.activeElement);
            if (event.key === "Home") {
                index = 0;
            } else if (event.key === "End") {
                index = items.length - 1;
            } else if (event.key === "ArrowDown") {
                index = (index + 1 + items.length) % items.length;
            } else {
                index = (index - 1 + items.length) % items.length;
            }
            items[index].focus();
        });
    }

    function boot() {
        enhance(document);
        document.addEventListener("click", function (event) {
            var target = event.target && event.target.closest ? event.target : null;
            if (!target) {
                return;
            }
            var button = target.closest(".author-action-btn");
            if (button) {
                if (menu && currentButton === button) {
                    closeMenu(false);
                } else {
                    openMenu(button);
                }
                return;
            }
            if (menu && !menu.contains(target)) {
                closeMenu(false);
            }
        });
        window.addEventListener("resize", function () {
            closeMenu(false);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot, { once: true });
    } else {
        boot();
    }
})();
