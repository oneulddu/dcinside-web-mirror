(() => {
  "use strict";

  const DELETED_TEXT = "이 댓글은 게시물 작성자가 삭제하였습니다.";

  const normalizeText = (text) =>
    (text || "")
      .replace(/[^\w\sㄱ-ㅎㅏ-ㅣ가-힣.?!]/g, "")
      .replace(/\s+/g, " ")
      .trim();

  const hasPatternRepeat = (text) => {
    const words = normalizeText(text).split(" ").filter(Boolean);
    const windowSize = 4;
    const counts = {};
    for (let i = 0; i <= words.length - windowSize; i += 1) {
      const key = words.slice(i, i + windowSize).join(" ");
      if (key.length < 10) {
        continue;
      }
      counts[key] = (counts[key] || 0) + 1;
      if (counts[key] >= 5) {
        return true;
      }
    }
    return false;
  };

  const getCommentText = (li) => {
    const textNode = li.querySelector(".comment-main p");
    return textNode ? textNode.innerText : "";
  };

  const isDcconComment = (li) => !!li.querySelector(".comment-main img.dccon");

  const runFilter = () => {
    const list = document.querySelector(".comment-list");
    const shell = document.querySelector(".comment-shell");
    if (!list || !shell) {
      return;
    }

    const items = Array.from(list.querySelectorAll(":scope > li"));
    if (!items.length) {
      return;
    }

    const normalized = items.map((li) => normalizeText(getCommentText(li)));
    const counts = normalized.reduce((acc, text) => {
      if (!text) {
        return acc;
      }
      acc[text] = (acc[text] || 0) + 1;
      return acc;
    }, {});

    const hidden = [];
    items.forEach((li, idx) => {
      const raw = getCommentText(li);
      const norm = normalized[idx];
      const deleted = norm === DELETED_TEXT;
      const repeated = norm && counts[norm] >= 3;
      const patternSpam = raw && hasPatternRepeat(raw);
      if ((repeated || patternSpam || deleted) && !isDcconComment(li)) {
        li.classList.add("comment-spam-hidden");
        hidden.push(li);
      }
    });

    if (!hidden.length) {
      return;
    }

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "comment-spam-toggle";
    btn.textContent = `도배 댓글 보기 (${hidden.length})`;

    let showing = false;
    btn.addEventListener("click", () => {
      showing = !showing;
      btn.textContent = showing
        ? "도배 댓글 숨기기"
        : `도배 댓글 보기 (${hidden.length})`;
      hidden.forEach((li) => {
        li.classList.toggle("comment-spam-hidden", !showing);
        li.classList.toggle("comment-spam-highlight", showing);
      });
    });

    const title = shell.querySelector("h2");
    if (title) {
      title.insertAdjacentElement("afterend", btn);
    } else {
      shell.prepend(btn);
    }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", runFilter, { once: true });
  } else {
    runFilter();
  }
})();
