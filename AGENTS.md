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

**Routes** (`app/routes.py`):

- Single Blueprint (`bp`) with all routes
- Key routes:
  - `/`: Home, heung gallery list, gallery search
  - `/recent`: Recently visited galleries
  - `/board?board=airforce&page=1`: Gallery post list
  - `/read?board=airforce&pid=12345`: Post reader
  - `/read/related`: Related-post JSON endpoint for infinite scroll
  - `/media`: Image/webp/dccon proxy
  - `/movie`: Video proxy
- Recent galleries are tracked through cookies, capped by `MIRROR_RECENT_MAX_ITEMS`

### Async Bridge Pattern

Routes use `async_bridge.run_async(coro)` to bridge Flask's sync context with async scraping. It detects an active event loop and either uses `asyncio.run()` or a `ThreadPoolExecutor` fallback.

### Frontend

- `templates/base.html`: Base layout with header, theme toggle, and nav tabs
- `templates/board.html`: Gallery list with filters, pagination, and read-state markers
- `templates/read.html`: Post reader, comments, embedded related posts, and infinite scroll target
- `static/javascript/read_state.js`: Dark mode and read-state persistence
- `static/javascript/read_related_loader.js`: Infinite scroll related-post loader
- `static/javascript/comment_spam_filter.js`: Client-side spam filtering
- `static/css/main.css`: SUIT font and responsive light/dark UI

### Frontend Skill Priority

When a user explicitly names a frontend skill or workflow, that named workflow takes priority over
general visual QA helpers.

- `$ux-first-fable`: first inspect the target screen, write or update `docs/ux-flow.md`, prepare
  `docs/fable-handoff.md`, and run the Fable/Claude Code handoff before making or finalizing UI
  changes when the CLI is available. If the handoff cannot run, report the exact blocker.
- Superloopy: treat as opt-in visual QA, not an automatic frontend owner. Use it only when the user
  explicitly asks for Superloopy/loopy, strict visual evidence, anti-slop auditing, or a
  Superloopy evidence trail.
- If both are explicitly requested, run the UX/Fable workflow first. Use Superloopy afterward as a
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
- `MIRROR_HEUNG_CACHE_TTL`, `MIRROR_HEUNG_CACHE_FILE`: Heung gallery cache settings
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
