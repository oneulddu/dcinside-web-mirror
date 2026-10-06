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
  (visible tab, online) and offers "새 글 N개" that replaces `#board-list` and dispatches `mirror:board-refreshed`.
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
