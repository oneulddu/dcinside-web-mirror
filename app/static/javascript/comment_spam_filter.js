(() => {
  "use strict";

  const DELETED_TEXT = "이 댓글은 게시물 작성자가 삭제하였습니다.";
  const SHORT_REACTION_REPEAT_THRESHOLD = 12;
  const SHORT_TEXT_REPEAT_THRESHOLD = 7;
  const NORMAL_REPEAT_THRESHOLD = 3;
  const SHORT_REACTION_PATTERN = /^(?:[ㅋㅎㅠㅜㅇㄴㄹㄷㄱㅅㅂㅈㅊㅌㅍㅁ]+|ㄹㅇ|ㅇㅇ|ㄴㄴ|ㄱㄱ|ㄷㄷ+|굿|헐)$/;

  const normalizeText = (text) => {
    const value = text || "";
    return (typeof value.normalize === "function" ? value.normalize("NFC") : value)
      .replace(/[^\p{L}\p{M}\p{N}\s.?!_]/gu, "")
      .replace(/\s+/g, " ")
      .trim();
  };

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

  // 포커고수 댓글은 여러 문단일 수 있어 본문 전체 글자로 비교한다.
  // 이미지 차단 버튼 글자는 빼고 세므로, 이미지만 있는 댓글은 글자가 없어 접히지 않는다.
  const pokerText = (body) => {
    const copy = body.cloneNode(true);
    copy.querySelectorAll("button").forEach((button) => button.remove());
    return copy.textContent || "";
  };

  const getCommentInfo = (li) => {
    const main = li.querySelector(".comment-main");
    const pokerBody = main ? main.querySelector(".poker-comment-body") : null;
    const textNode = pokerBody ? null : (main ? main.querySelector("p") : null);
    return {
      element: li,
      text: pokerBody ? pokerText(pokerBody) : (textNode ? textNode.textContent : ""),
      hasDccon: !!(main && main.querySelector("img.dccon"))
    };
  };

  const isRepeatedTextSpam = (text, count) =>
    Boolean(text) && count >= getRepeatThreshold(text);

  // 댓글이 나중에 붙으면(포커고수 이전 댓글) 다시 계산한다. 버튼은 하나만 두고 펼침 상태를 이어 간다.
  const state = { button: null, hidden: [], showing: false };

  const labelButton = () => {
    const btn = state.button;
    btn.setAttribute("aria-expanded", state.showing ? "true" : "false");
    btn.textContent = state.showing
      ? "접힌 댓글 숨기기"
      : `접힌 댓글 보기 (${state.hidden.length})`;
  };

  const runFilter = () => {
    const list = document.querySelector(".comment-list");
    const shell = document.querySelector(".comment-shell");
    if (!list || !shell) {
      return;
    }

    const comments = Array.from(list.querySelectorAll(":scope > li")).map(getCommentInfo);

    const normalized = comments.map((comment) => normalizeText(comment.text));
    const counts = normalized.reduce((acc, text) => {
      if (!text) {
        return acc;
      }
      acc[text] = (acc[text] || 0) + 1;
      return acc;
    }, {});

    const normalizedDeletedText = normalizeText(DELETED_TEXT);
    const hidden = [];
    comments.forEach((comment, idx) => {
      const raw = comment.text;
      const norm = normalized[idx];
      const deleted = norm === normalizedDeletedText;
      const repeated = isRepeatedTextSpam(norm, counts[norm] || 0);
      const patternSpam = raw && hasPatternRepeat(raw);
      if ((repeated || patternSpam || deleted) && !comment.hasDccon) {
        hidden.push(comment.element);
      }
    });

    state.hidden.forEach((li) => {
      if (!hidden.includes(li)) {
        li.classList.remove("comment-spam-hidden", "comment-spam-highlight");
      }
    });
    state.hidden = hidden;

    if (!hidden.length) {
      if (state.button) {
        state.button.remove();
        state.button = null;
        state.showing = false;
      }
      return;
    }

    hidden.forEach((li) => {
      if (state.showing) {
        li.classList.remove("comment-spam-hidden");
        li.classList.add("comment-spam-highlight");
      } else {
        li.classList.add("comment-spam-hidden");
      }
    });

    if (state.button) {
      labelButton();
      return;
    }

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "comment-spam-toggle";
    if (list.id) {
      btn.setAttribute("aria-controls", list.id);
    }
    state.button = btn;
    labelButton();

    btn.addEventListener("click", () => {
      state.showing = !state.showing;
      labelButton();
      state.hidden.forEach((li) => {
        li.classList.toggle("comment-spam-hidden", !state.showing);
        li.classList.toggle("comment-spam-highlight", state.showing);
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
  document.addEventListener("poker:comments-added", runFilter);
})();
