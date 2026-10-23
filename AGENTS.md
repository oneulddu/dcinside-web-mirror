# AGENTS.md
Use Korean to communicate with users.

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

DCinside Web Mirror is a Flask-based read-only mirror for DCinside galleries. It scrapes gallery pages asynchronously, rewrites media through a safe proxy, and renders a clean reading UI.

## Development Commands

```bash
# Install runtime dependencies
make install

# Install development/test dependencies
make install-dev

# Run tests
make test

# Run development server (Flask, auto-reload)
make run
# Default: http://0.0.0.0:8080

# Run production server (Gunicorn)
make run-prod

# PM2 process management
pm2 start ecosystem.config.js
pm2 restart dc-mirror
pm2 logs dc-mirror
```

## Architecture

### Application Factory Pattern

- `app/__init__.py`: `create_app()` initializes Flask with environment-based config
- `wsgi.py`: WSGI entry point for Gunicorn
- `run.py`: Development server entry point

### Core Components

**Services Layer** (`app/services/`):

- `dc/api.py`: Async DCinside scraper using `aiohttp` and `lxml`
  - Data models: `DocumentIndex`, `Document`, `Comment`
  - Main runtime methods: `API.board()`, `API.document()`, `API.comments()`
  - Uses mobile and PC DCinside HTML fallbacks where needed
- `core.py`: Business logic with thread-safe caching
  - `async_index_with_head_categories()`: Gallery post list plus head category tabs
  - `async_read()`: Single post with comments, images, and embedded related posts
  - `async_related_after_position()`: JSON related-post loading for infinite scroll
  - Caches: board pages, latest IDs, and author codes with TTL
- `heung.py`: Heung gallery retrieval/search with memory and file cache
- `html_sanitizer.py`: Body HTML allowlist cleanup and media URL rewriting
- `media_proxy.py`: `/media` and `/movie` response builders with SSRF-oriented checks
- `recent.py`: Cookie-based recent gallery tracking with server-side helper cache
- `async_bridge.py`: `run_async(coro)` bridge from Flask sync routes to async scraping

**Routes** (`app/routes.py` and `app/poker_routes.py`):

- `main` Blueprint owns DC routes; `poker` Blueprint owns the isolated Pokergosu routes
- Key routes:
  - `/`: Home, heung gallery list, gallery search
  - `/recent`: Recently visited galleries
  - `/board?board=airforce&page=1`: Gallery post list
  - `/read?board=airforce&pid=12345`: Post reader
  - `/read/related`: Related-post JSON endpoint for infinite scroll
  - `/media`: Image/webp/dccon proxy
  - `/movie`: Video proxy
- Recent galleries are tracked through cookies, capped by `MIRROR_RECENT_MAX_ITEMS`

### DC write/vote backend and configuration

- Optional fixed-nickname account mode has two slots: `"1"` uses `MIRROR_DC_LOGIN_ID` and
  `MIRROR_DC_LOGIN_PASSWORD`; optional `"2"` uses `MIRROR_DC_LOGIN_ID_2` and
  `MIRROR_DC_LOGIN_PASSWORD_2` from the worker's startup environment. A slot is configured only
  with both variables non-empty. Python checks presence only; never send credentials in
  worker stdin, logs or responses. `/write/account`, `/write/account/status` and `/write/account/login`
  share the unlock/no-store gate. `/write/account/status` rejects any supplied non-same-origin
  Sec-Fetch-Site; the `/write/account` page also accepts `none` (address bar, bookmark).
  Explicit and lazy login attempts across both slots (including failures) share one worker-owned 10s interval, counted
  at attempt start. Cooldown returns `retry`/HTTP 429 without contacting DC. OTP and login captcha
  failures block lazy login for that slot until an explicit login succeeds. Status never contacts DC.
  `account_status` returns the six public fields (`configured`, `logged_in`, `user_id`, `name`,
  `blocked`, `reason`) for slot 1 at the top level for compatibility, plus `accounts` keyed only
  by configured slot ids, each with the same six fields. Never serialize library login sessions.
  Login sessions and OTP tokens are memory-only: account login requires a single web process
  (`MIRROR_WORKERS=1`), because each process owns its worker. Worker restart loses login state.
  Account write/comment/delete/modify use `mode: "account"` and selector `account`: absent defaults
  to `"1"`; a supplied selector must be exactly string `"1"` or `"2"` and configured, otherwise
  HTTP 400 `invalid`/field `mode` (worker `AccountInvalid`, `sent:false`). Anonymous mode ignores it.
  Worker sessions are `{type:"login", account:"1"|"2"}`; explicit `account_login` takes `account`.
  Modify-load returns the mode and selected account for submit. Login success returns the selected
  slot's public fields and `account`; `login_needed` includes `account` and an `#account-N` link.
  Sessions, OTP tokens and failure state are isolated per slot. Never fall back or retry with the other slot.
  Login expiry disables library automatic relogin/resend and returns `login_needed`/`expired` only
  after DC answers `certification_login` (not an ambiguous transport failure), clearing only that slot.
  Never auto-resubmit.
  Explicit decision: retain the existing library app_id refresh and worker
  “잠시후 다시 이용해주세요.” app_id-settling retry behavior unchanged.
- Account frontend: one header author menu (`base.html` `.author-pick-control` + `author_menu.js`,
  shown only when `write_enabled` and ≥1 slot; `body[data-author-slots]` lists slots) chooses 유동 or
  `account:N` for writes, comments and votes alike. Labels are "디시 계정" when only one slot, else
  "계정 N", with " · <DC nickname>" once logged in; never the login id. The choice lives in
  localStorage `mirror_write_mode_v1` (`MirrorDcPost.authorMode/setAuthorMode/onAuthorChange`; legacy
  `account` reads as `account:1`); other tabs follow via the `storage` event. Forms have no author
  toggle; they show the current author and react live, keeping drafts. A remembered slot that is no
  longer offered is not replaced: nothing is sent and the user re-chooses in the header (with no menu
  left, the first submit switches to 유동 and asks to press again). Account mode sends
  `mode: "account"`, `account: "N"` and no nickname/password; the author is captured at submit time and
  a late response is handled for the author it was sent as. Account votes never pre-open the
  anonymous captcha and always use the app captcha (`setSource`); changing author clears a pending
  vote captcha. Modify keeps the mode and slot it was opened with
  (`/write?...&mode=account&account=N`) and ignores the header menu. Pages fetch `/write/account/status` at most once (`MirrorDcPost.accountStatus`, never
  contacts DC). The read page adds password-free 수정·삭제 (`data-owner="account"`, `data-account="N"`)
  to items whose author code equals a logged-in slot's `user_id` (slot 1 wins on a collision); new
  account comments carry their slot on the delete button. This is display only; DC decides ownership,
  and a failed action is never retried with the other slot. `login_needed` is not `uncertain`: show
  the message with a new-tab link to `account_url` (`/write/account#account-N`) and keep the draft.
  `/write/account` renders one form per configured slot (`account-N-*` ids, own OTP/captcha state);
  `account_login.js` logs in only on explicit submit with `{account: "N"}` and asks for OTP/login
  captcha only when DC requires them.

- DC anonymous writing (`GET/POST /write`) and voting (`POST /read/vote`, `direction` `up`|`down`) are opt-in.
  `MIRROR_WRITE_KEY` unset disables every write route; `/write/unlock` issues a signed,
  HttpOnly 180-day cookie, and `/write/lock` clears it. POSTs require same-origin headers.
  Five failed unlock attempts per process per minute are allowed; successful writes/votes
  have process-wide 20s/3s intervals (up and down votes keep separate intervals). Never log nicknames, passwords, unlock keys or captcha answers.
- DC comments (`POST /read/comment`: board, kind, pid, content 1-400 chars, nickname, password, optional
  `parent_id` + `parent_author_code`, captcha) use the worker `comment` op, which calls app API
  `comments.write` (`comment_ok.php`) through an anonymous session like `write`. Replies send
  `{comment_no, user_id}`; `user_id` is the parent's author code only when it is a DC id (`[A-Za-z0-9_]{1,40}`),
  otherwise empty (anonymous/IP parents). Comment captchas use the app image `/write/captcha?kind=comment`.
  Successful comments have a 10s process-wide interval; an unknown result after sending is `uncertain`.
- DC anonymous delete/modify reuse the same gate, same-origin check and `perform`: `POST /read/delete` (board, kind, pid,
  password → `board_url`), `POST /read/comment/delete` (+ `comment_id`), `POST /write/modify/load` (worker `modify_info`,
  app API `gall_modify.php` with the password; returns subject, head_id, ordered blocks `text`/`photo`/`html`, and a sha256
  `fingerprint` of subject+memo+file), and `POST /write` with `pid` + `fingerprint` + `photos` (`{"keep": memo index}` |
  `{"new": upload index}`) + `originals` (kept html-block indexes). Body lines `[사진 N]`/`[원본 N]` place photos[N-1] /
  originals[N-1] (`dc_write.photo_blocks(..., photos=, originals=)`); unmarked ones go to the end. The worker `modify` op
  re-fetches the snapshot, refuses (`stale`, not sent) when the fingerprint differs, and re-sends kept blocks only from that
  fresh snapshot, never client HTML. Secret posts and posts with polls, videos or AI images are `not_editable` because the
  app write would drop them. Live facts (gallery `prewriting`): memo values are entity-escaped HTML; wrong passwords answer
  `비밀번호 오류` (modify/post delete) and `비밀번호가 틀립니다.` (comment delete); comment writes return the new number in
  `data`, surfaced as `comment_id`. `isPhotoBlock`/`deletedFiles` in `dc_app/worker.mjs` hold the photo-memo format.
  Intervals: modify 20s, delete/comment_delete 3s, modify_info 1s.
- `app/services/dc_write.py` serializes a lazy Node worker with a shared 40s deadline (55s for image writes) including both lock waits and discards
  it on crash/timeout/protocol failure without replaying a potentially completed write.
  Write/vote transport failures after sending return HTTP 503 with code `uncertain`; failures before sending return `unavailable`.
  `dc_app/worker.mjs` uses pinned `@green-1052/dcinside.js@2.0.1`, stores device credentials
  atomically with mode 0600, and waits for newly issued app IDs to settle. Captcha images
  are image-only, at most 200KB, and no-store. Never bypass upstream captcha.
- `/write` also accepts multipart `payload` + `images`: up to 10 magic-validated JPG/PNG/GIF/WEBP files, 10MiB each/30MiB total, with a 32MiB request cap.
  A body line that is only `[사진 N]` places photo N there (`dc_write.photo_blocks` → worker `blocks`); unknown or
  duplicate markers are body errors, unmarked photos follow `image_position`, and marker text without photos stays text.
  Uploads use private temporary directories under `MIRROR_DC_APP_UPLOADS` (system temp by default), are removed after every outcome, and are read before worker DC calls; image-only bodies and top/bottom placement are supported.
- `MIRROR_DC_APP_NODE` selects Node (default PATH lookup), `MIRROR_DC_APP_DIR` defaults to
  `<repo>/dc_app`, and `MIRROR_DC_APP_STATE` defaults to `instance/dc_app_credentials.json`.
  Set `MIRROR_WRITE_KEY` only in the server environment. Deployment installs `dc_app` with
  `npm ci --omit=dev`; use Node 24. Live checks may only use `write` with `validateOnly: true`;
  upvote has no validation mode. Pytest must use a fake manager and never contact DC.
- DC silently rejects votes from cloud/datacenter IPs ("추천 할수 없습니다." without `carrier_vpn_ban`),
  so `MIRROR_DC_APP_PROXY` (an `http://relay:<token>@<tailscale-ip>:<port>` URL) sends every worker request,
  including app_id issuance and captcha images, through the home relay `scripts/dc_relay.mjs`. Only the worker
  gets `NODE_USE_ENV_PROXY`/`HTTPS_PROXY`; scraping and media stay direct. The relay accepts CONNECT to
  `*.dcinside.com`, `*.dcinside.co.kr` and the three app-registration Google hosts on 443 only, from
  `DC_RELAY_ALLOW_FROM` with the token. Before write/vote sends, `perform` TCP-checks the relay and returns
  `unavailable`/`relay_down` (not sent) when it is offline. Production relay: Mac mini LaunchAgent
  `com.oneul.dc-relay` running a copy at `~/Library/Application Support/dc-relay/` (copy the script again after
  changes), log `~/Library/Logs/dc-relay.log`. A repeated upvote answers "추천은 1일 1회만 가능합니다.", which
  verifies the route without adding a vote.
- Account votes (`/read/vote` with `mode:"account"`, `account`) run with that slot's login session like
  writes (same selector rules, lazy login, shared cooldown, slot-only expiry); the library sends
  `confirm_id` = the slot's `user_id`. They never use the PC flow: captcha answers go as app captcha and
  `captcha_required` returns `kind=recommend` image URLs; the worker rejects login session + `pc`.
  Vote intervals stay shared across identities.
- Anonymous votes that carry a captcha answer use the PC web flow (`dc_app/pc_vote.mjs`) for normal/minor/mini galleries:
  `pc_vote_captcha` opens the PC view page, keeps its cookies and `_view_form_` values under the client's 16-hex key
  (5 minutes, one use), and returns `/kcaptcha/image_v3/` with translucent noise palette entries turned opaque white.
  The answer is POSTed to `/board/recommend/vote` with `ci_t`, `mode` U/D, `code_recommend` and the
  `<gall><no>_Firstcheck[_down]` cookie. A wrong code or expired key becomes `captcha_required` with a new key.
  App captchas stay for writes and person galleries because their noise is opaque. Vote captcha image URLs are
  `/write/captcha?kind=vote&board&key&pid&gallery_kind`.

### Pokergosu Mirror

- `/poker` opens the board reader; `/poker/<board_id>` lists posts and
  `/poker/<board_id>/<pid>` reads a post with the source page preserved.
- `app/services/poker_boards.py` defines the ten-board allowlist and validates upstream links.
- `app/services/pokergosu.py` uses synchronous `curl_cffi` sessions and `lxml`.
  Keep these calls outside the shared DC async loop. Cache and in-flight keys include the board.
  Sessions are thread-local and discarded on transport errors, oversize bodies, or challenges.
  Cooldown and start pacing are shared across Gunicorn workers through an `fcntl`-locked state
  file (`MIRROR_POKER_STATE_FILE`); keep the same inode and fall back to process-local state on OSError.
  A challenge/403/429 on an earlier-comment fetch does not start the global 60s cooldown;
  it pauses only comment fetches for `MIRROR_POKER_COMMENTS_BLOCK_SECONDS` (default 6h) in the same
  state file (`comment_api_blocked_until`). Reads then skip automatic comment collection, and the endpoint returns
  `code: "comments_blocked"` so the client stops without a retry. Do not try to bypass the challenge.
  Transient 502/503 failures may return expired successes with `stale: True` within
  `MIRROR_POKER_STALE_SECONDS`; 400/403/404 never do. Templates and JSON surface that as a notice.
  Routes pass `prepare` callables so sanitized HTML is cached; body and comment preparers differ
  (only the body keeps exact YouTube embeds, normalized to `youtube-nocookie.com`).
- Search is `/poker/<board_id>/search?s=1..5&v=` (news excludes 5, 2-20 chars) and validated
  in `poker_boards.validate_search` before any fetch. Search context (`search`) follows read,
  footer list, back links, and error retry URLs. Cache keys include board, s, v, and page.
- Recent galleries (home tiles and `/recent`) are DC-only. Poker routes never write the recent
  cookie, and `normalize_recent_entry` drops legacy `poker:<id>` / `kind="poker"` rows.
- `scripts/poker_smoke.py` checks every public board against the live source.
- Eight boards are public; `groupbuy` and `qna` currently redirect to upstream login and show
  a 403 explanation. Login redirects do not trigger the Cloudflare cooldown.
- `app/services/poker_media.py` sanitizes content and serves signed, allowlisted images through
  the pinned media transport. Do not expand the DC media allowlist for Pokergosu.
  Preserve validated image width/height pairs for lazy layout. Keep the four-slot, one-second
  wait bound; errors are no-store and logged without source paths or signatures.
  `poker_images.js` retries failed body/comment images at most twice with backoff and two
  retry slots. Load it before image hydration; retries must not reveal hidden images.
- Templates live in `app/templates/poker/` with scoped `app/static/css/poker.css`.
  News uses a separate grid parser; missing metadata stays absent.
- Post HTML opens at the last upstream comment page. `poker_comments.js` automatically loads
  earlier pages through `/poker/<board_id>/<pid>/comments?cpage=N`, one request at a time.
  The server fetches them from the upstream post page's own JSON path
  (`/api2/board/getcommnet/<board>/<pid>/<page>/25/xpage/20/undefined/undefined/0`, `parse_comment_api`);
  upstream challenges `?cpage=` post URLs. Rows must match `parse_post` (`C<srl>` ids, direct parent,
  depth-first order, cboard wrapper) and skip comments upstream hides (`uploaded_count` 9997/9998, `blind`).
  Infer comment page only from the `#comment` pager, keep cache keys board/post/page-specific,
  validate the returned page, sanitize every comment, and deduplicate IDs when prepending.
  Failed or ambiguous collection stays partial; never delay the initial post for extra pages.
  Newly added comment images must respect the existing image-block setting.
- Shared `read_state.js` stores Pokergosu posts as `poker:<pid>` alongside unchanged DC keys.
  Only successful article views mark Poker posts read; footer list updates must reapply read state.
  Upstream `file.gif` means an attachment, not necessarily a photo. Keep that label distinction.

### Visitor check

- `app/services/visitor_check.py` implements an opportunistic JS-cookie gate before protected views.
  HTML GET navigations get a 200/no-store challenge (`app/templates/visitor_check.html`);
  `app/static/javascript/visitor_check.js` stores the signed, constant `v1` cookie for 180 days
  and reloads, with at most two automatic attempts per tab. HEAD stays bodyless and does not
  set cookies or call upstream. Only static files, healthz and favicon are exempt.
- `MIRROR_VISITOR_CHECK=1` enables it (production default); development/tests default off.
  `0` or `off` disables it. It does not bind tokens to IP or UA and uses no ProxyFix.
- JSON/JS requests receive 403 with `ok: false`, `code: "visitor_check_required"`,
  Korean `message`/`error` and `sent: false`: nothing reached the protected view.
  Preserve input, ask the user to refresh, never auto-resubmit or label this `uncertain`.
  Loaders offer a refresh action and board polling stops. Form POSTs get a terminal 403
  with a safe GET link, never a challenge or POST replay; media gets a non-HTML 403.
- HTML fetches from page routes must send `X-Mirror-Fetch: 1` (board updates/return refresh);
  marked requests get JSON errors rather than an HTML challenge. No UA/Referer heuristics.
- This is a deterrent, **not bot authentication**: a scraper can extract the token from
  the challenge HTML without running JavaScript. There is deliberately no obfuscation.

### Async Bridge Pattern

Routes use `async_bridge.run_async(coro)` to bridge Flask's sync context with async scraping. It detects an active event loop and either uses `asyncio.run()` or a `ThreadPoolExecutor` fallback.

### Frontend

- `templates/base.html`: Base layout with header, theme toggle, and nav tabs
- `templates/board.html`: Gallery list with filters, pagination, and read-state markers
- `templates/read.html`: Post reader, comments, embedded related posts, and infinite scroll target
- `static/javascript/read_state.js`: Dark mode and read-state persistence
- `static/javascript/read_related_loader.js`: Infinite scroll related-post loader
- `static/javascript/comment_spam_filter.js`: Client-side spam filtering
- `static/javascript/board_updates.js`: Polls `/board/updates` every ~60s on the plain page-1 board list only
  (online) and offers "새 글 N개" that replaces `#board-list` and dispatches `mirror:board-refreshed`.
  Hidden tabs keep checking every 180-210s (never below 180s, no immediate check on hide) and stop after 30 minutes
  hidden. `document.title` gets a single `(N)`/`(N+)` prefix from the same count; zero or list replacement removes it.
- `static/javascript/board_keyboard.js`: DC board list J/K select, O/Enter open, Esc clear. Uses `event.code`
  so Korean IME keys work; ignores inputs, modifiers, composition, open dialogs and `#media-block-menu`/`#author-menu`,
  busy lists, and `.is-user-filtered` rows. Selection is real link focus plus `.is-keyboard-current`.
- `static/javascript/image_viewer.js`: Read-page `<dialog>` viewer for visible `#article-body img.body-image`
  only (has `src`, not hidden, not inside a link). It never reads `data-body-image-src` itself, so the image-block
  setting stays authoritative; a viewed image that becomes blocked closes the viewer. Comments and related posts are out of scope.
- DC write/vote frontend: `templates/write.html` + `static/javascript/write_form.js`, `templates/write_unlock.html`,
  and the read-page `#vote-panel` + `static/javascript/vote.js` (rendered only when `write_enabled`). Both use
  `static/javascript/dc_post_client.js` for JSON POSTs, response codes, same-origin redirects, and captcha boxes
  (`_bits.captcha_box`). Never auto-resend a write or vote; an unknown result tells the user to check the list.
  Captcha refresh makes a new random 16-hex key client-side and only re-fetches `/write/captcha`.
  Nickname/password are saved in `localStorage["mirror_write_identity_v1"]` only after opt-in checkboxes.
  The read page comment composer (`#comment-form` + `static/javascript/comment_form.js`, only when `write_enabled`;
  `write_configured` alone shows an unlock link) shares that identity key. Top-level comments with numeric ids get a
  "답글" button that moves the form into a `li.comment-reply-slot` after the thread; replies to replies are not offered.
  Success appends the comment client-side with time "방금" and bumps `.comment-count` instead of reloading.
  `vote.js` reads `GET /read/vote/options` (worker `vote_info`: app API `view_main.nonrecomm_use` and
  `view_info.recommend_captcha`, cached per post for 10 minutes because galleries can disable downvotes per 말머리) and shows or hides the downvote button from it.
  Despite its name, `nonrecomm_use: true` means downvotes are disabled (checked against PC view pages). Before options load, the button follows the
  scraped `votedown_count`. A cached `down: false` makes `/read/vote` reject downvotes without contacting DC, and
  `captcha: true` opens the captcha box on the first press instead of sending. Rejected votes show DC's own cause
  after "디시 안내:"; `carrier_vpn_ban` becomes `reason: ip_blocked`. Vote and write outcomes are logged as
  op/board/no/code/reason/cause only. The article meta line shows 비추천 N next to 추천 when the scraped count is a number and hides it
  together with the button. Vote success adds one to that button's count and the matching meta count.
  The write page lays title/body/photo strip out as one document between lines; 말머리 are radios in a text row.
  Adding photos inserts `[사진 N]` lines at the last body caret; removing renumbers them, while strip arrows swap
  which photo fills each marker (photos trade places in the post). The client sends `image_position: bottom`.
  The board masthead `#write-entry` and footer unlock/lock links
  appear only when `write_configured`; Poker pages never show them. Error styling uses `--fg`, never `--hot`.
  Images: `#write-images` keeps an ordered client list (`pickImages`/`moveItem` are pure and tested), sends
  multipart (`payload` JSON + ordered `images` + `image_position`) through `MirrorDcPost.postForm` (XHR progress;
  disconnect before upload completes is reported as not sent; bare 413 becomes an images error). Files the browser
  cannot preview block submission until removed. For browser QA, run the dev server with
  `MIRROR_DC_APP_NODE=/usr/bin/false` so an unstubbed submit can never reach DC.
- DC delete/modify frontend (only when `write_enabled`): guest authors (author code like `1.234`, `is_guest` macro in
  `read.html`) get "수정 · 삭제" in the article meta and "삭제" on comments with numeric ids. `static/javascript/post_manage.js`
  opens one password `<dialog>` (prefilled from the saved identity) for post and comment deletes; post success navigates to
  `board_url`, comment success removes the `li` (or leaves "삭제된 댓글이에요." when replies exist), decrements the count and
  dispatches `mirror:comment-removed`, which `comment_form.js` uses to drop a stale reply target. New comments get their
  `comment_id` and a delete button. `/write?pid=N&author=` is the modify mode of `write.html`/`write_form.js`: the editor stays
  hidden until "글 불러오기" loads the post with the password (then read-only); kept photos share the photo strip with new
  uploads (only new ones count toward the 10-photo limit and are uploaded), non-photo blocks are `[원본 N]` lines with a
  removable list, and the nickname is prefilled from `author` because modify sends it as the post name.
- `static/javascript/comment_search.js`: read-page comment filter over already loaded comments (body, nickname, author code;
  no DC request). Hidden rows get `is-search-hidden`; counts skip spam/user-filtered rows; `data-partial` (from
  `comments_complete`) adds a note that only loaded comments were searched. Esc clears.
- `static/javascript/user_filter.js`: DC-only block filter (nickname, author code, guest IP, title word) stored in
  `localStorage["mirror_user_filter_v1"]`. It reads `data-author`/`data-author-code` from the `author_text` macro
  and the related loader; keep those attributes when changing author markup. Poker pages do not load it.
- `static/javascript/author_actions.js`: Read-page author menu (search by original nickname via
  `data-author-search-name`, memo, block). List rows stay full links without author buttons.
  DC comment `@nickname` mentions also open this menu. Match complete original nicknames before
  copying an observed author code; ambiguous/unknown names must remain name-only. Skip email and URL links.
- `static/javascript/user_memo.js`: Per-author memos in `localStorage["mirror_user_memo_v1:<identity>"]`
  (identity `code:<author_code>` or `name:<author_search_name>`), rendered next to DC authors only.
- DC notice tab: `/board?notice=1` and `/read?notice=1` drop recommend/headid/search (mixed URLs redirect
  before fetching). Notice lists are one page; missing author/time/votes stay `None` and templates omit them.
  Notice reads render no related section and `/read/related?notice=1` returns empty without upstream calls.
  Normal and recommended lists exclude pinned notices. Board cache keys include the notice flag.
  `board.html` exposes `data-notice`; `read.html` exposes board context on `[data-board-context]`.
- DC search pagination carries a validated signed `search_pos` through list/read/related/time URLs and
  list-context caches. Upstream mobile calls use `s_pos`; `dc/search.py` parses adjacent pages and
  segment transitions. Search lists stay on mobile because PC search can return a different page size.
  Clamped search pages redirect without caching duplicate rows. New searches and filter changes reset
  the cursor; related rows carry their own `(source_page, search_pos)` across segment boundaries.
- `static/css/main.css`: SUIT font and responsive light/dark UI

### Frontend Skill Priority

When a user explicitly names a frontend skill or workflow, that named workflow takes priority over
general visual QA helpers.

- `$ux-first-fable`: for substantial UI work, inspect the target screen and update `docs/ux-flow.md`
  with the UX contract. Select the installed skill's path from the actual primary model:
  Astra owns planning, backend, integration, and browser acceptance on the Astra path;
  Fable owns frontend and integration on the Fable path. Use Astra for former Sol roles.
  Prepare `docs/fable-handoff.md` only for an actual Fable handoff. The historical skill name
  does not require launching Claude Code or switching away from an Astra primary.
  Use available delegation tools and the skill's bounded worker roles. Report a required
  unavailable role precisely while continuing independent authorized work.
- Superloopy: treat as opt-in visual QA, not an automatic frontend owner. Use it only when the user
  explicitly asks for Superloopy/loopy, strict visual evidence, anti-slop auditing, or a
  Superloopy evidence trail.
- If both are explicitly requested, run the selected UX workflow first. Use Superloopy afterward as a
  verification gate for tokens, anti-slop checks, browser screenshots, and evidence files.
- For ordinary UI edits without a named workflow, follow `DESIGN.md`, keep changes scoped, and run
  real browser checks when visual quality is part of the task.

## Configuration

Environment variables use the `MIRROR_` prefix:

- `MIRROR_ENV`: `development` or `production` (default: `production`)
- `MIRROR_HOST`, `MIRROR_PORT`: Dev server bind (default: `0.0.0.0:8080`)
- `MIRROR_BIND`: Gunicorn bind (default: `[::]:6100`)
- `MIRROR_WORKERS`, `MIRROR_THREADS`, `MIRROR_TIMEOUT`: Gunicorn process/thread/timeout settings
- `MIRROR_HTTP_TIMEOUT`: DCinside request timeout (default: 20s)
- `MIRROR_HEUNG_CACHE_TTL`, `MIRROR_HEUNG_CACHE_FILE`, `MIRROR_HEUNG_REFRESH_RETRY_SECONDS`: Heung gallery cache and refresh-failure backoff settings
- `MIRROR_BOARD_PAGE_CACHE_TTL`: Short board-page cache TTL
- `MIRROR_BOARD_FILL_AUTHOR_CODES`: Enable cached board-list author code backfill
- `MIRROR_RELATED_PAGE_PROBE_STEPS`, `MIRROR_RELATED_TAIL_PAGES`: Related-post probing limits
- `MIRROR_MEDIA_*`: Media proxy cache, size, streaming, redirect, and allowlist settings
- `MIRROR_RECENT_*`: Recent-gallery cookie and server helper cache settings
- `MIRROR_SECRET_KEY`: Flask secret key, required for safe production operation

Config classes live in `app/config.py`: `DevelopmentConfig` (`DEBUG=True`) and `ProductionConfig` (`DEBUG=False`).

## Deployment

Production uses PM2 + Gunicorn:

- `ecosystem.config.js`: PM2 config with file watching and auto-restart
- `gunicorn.conf.py`: Multi-worker threaded Gunicorn config
- GitHub Actions auto-deploy on push to `main`
- PM2 cwd: `/home/ubuntu/mirror` (differs from repo checkout path `/home/ubuntu/workspace/mirror`)

## Key Patterns

1. Async scraping: DCinside calls use `aiohttp` and are coordinated from `core.py`
2. Multi-level caching: heung galleries, board pages, latest IDs, author codes, and recent-gallery helpers
3. Related posts: `/read/related` uses `async_related_after_position()` to continue after the last loaded post
4. Media proxying: server-side `/media` and `/movie` proxy paths handle DCinside referrer/CORS issues and apply host/content checks
5. Cookie-based recent galleries: client cookie storage with server-side normalization and deduplication
