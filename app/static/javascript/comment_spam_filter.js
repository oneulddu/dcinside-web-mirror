(() => {
  "use strict";

  const DELETED_TEXT = "이 댓글은 게시물 작성자가 삭제하였습니다.";
  const SHORT_REACTION_REPEAT_THRESHOLD = 12;
  const SHORT_TEXT_REPEAT_THRESHOLD = 7;
  const NORMAL_REPEAT_THRESHOLD = 3;
  const SHORT_REACTION_PATTERN = /^(?:[ㅋㅎㅠㅜㅇㄴㄹㄷㄱㅅㅂㅈㅊㅌㅍㅁ]+|ㄹㅇ|ㅇㅇ|ㄴㄴ|ㄱㄱ|ㄷㄷ+|굿|헐)$/;

  const normalizeText = (text) =>
    (text || "")
      .replace(/[^\w\sㄱ-ㅎㅏ-ㅣ가-힣.?!]/g, "")
      .replace(/\s+/g, " ")
      .trim();

  const compactText = (text) => normalizeText(text).replace(/\s+/g, "");

  const getRepeatThreshold = (text) => {
    const compact = compactText(text);
    if (!compact) {
      return Infinity;
    }
    if (compact.length <= 4 && SHORT_REACTION_PATTERN.test(compact)) {
      return SHORT_REACTION_REPEAT_THRESHOLD;
    }
    if (compact.length <= 6 && !/\s/.test(text)) {
      return SHORT_TEXT_REPEAT_THRESHOLD;
    }
    if (compact.length < 10) {
      return Math.max(SHORT_TEXT_REPEAT_THRESHOLD, 6);
    }
    return NORMAL_REPEAT_THRESHOLD;
  };

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

  const isRepeatedTextSpam = (text, count) =>
    Boolean(text) && count >= getRepeatThreshold(text);

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

    const normalizedDeletedText = normalizeText(DELETED_TEXT);
    const hidden = [];
    items.forEach((li, idx) => {
      const raw = getCommentText(li);
      const norm = normalized[idx];
      const deleted = norm === normalizedDeletedText;
      const repeated = isRepeatedTextSpam(norm, counts[norm] || 0);
      const patternSpam = raw && hasPatternRepeat(raw);
      if ((repeated || patternSpam || deleted) && !isDcconComment(li)) {
        hidden.push(li);
      }
    });

    if (!hidden.length) {
      return;
    }

    hidden.forEach((li) => {
      li.classList.add("comment-spam-hidden");
    });

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "comment-spam-toggle";
    btn.setAttribute("aria-expanded", "false");
    btn.textContent = `접힌 댓글 보기 (${hidden.length})`;

    let showing = false;
    btn.addEventListener("click", () => {
      showing = !showing;
      btn.setAttribute("aria-expanded", showing ? "true" : "false");
      btn.textContent = showing
        ? "접힌 댓글 숨기기"
        : `접힌 댓글 보기 (${hidden.length})`;
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
