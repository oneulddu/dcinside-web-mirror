# AGENTS.md
Use Korean to communicate with users

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

DCinside Web Mirror - Flask-based proxy viewer for DCinside galleries with async scraping and clean UI.

## Development Commands

```bash
# Install dependencies
make install

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
- `dc_api.py`: Async DCinside scraping (aiohttp + lxml)
  - `DocumentIndex`: Post data model
  - Functions: `get_gallery_posts()`, `get_document()`, `search_gallery()`
  - Uses mobile User-Agent to scrape DCinside HTML
- `core.py`: Business logic with thread-safe caching
  - `async_index()`: Gallery post list with pagination
  - `async_read()`: Single post with comments
  - `async_related_by_position()`: Smart related posts (probes pages around current post ID)
  - Caches: `_LATEST_ID_CACHE`, `_RELATED_CACHE`, `_AUTHOR_CODE_CACHE` with TTL

**Routes** (`app/routes.py`):
- Single Blueprint (`bp`) with all routes
- Key routes: `/` (home), `/board/<board_id>` (list), `/read/<board_id>/<doc_id>` (post)
- `/media-proxy`: Proxies DCinside images (avoids CORS/referrer issues)
- `/api/related`: JSON endpoint for infinite scroll
- Heung gallery: File-based cache (`instance/heung_gallery_cache.json`)
- Recent galleries: Cookie-based tracking (max 30)

### Async Bridge Pattern
Routes use `_run_async(coro)` to bridge Flask's sync context with async scraping. Detects running event loop and either uses `asyncio.run()` or submits to `ThreadPoolExecutor`.

### Frontend
- `templates/base.html`: Base layout with header, theme toggle, nav tabs
- `static/javascript/read_state.js`: Dark mode persistence (localStorage)
- `static/javascript/read_related_loader.js`: Infinite scroll for related posts
- `static/javascript/comment_spam_filter.js`: Client-side spam filtering
- `static/css/main.css`: Pretendard font, premium dark theme

## Configuration

Environment variables (prefix: `MIRROR_`):
- `MIRROR_ENV`: `development` or `production` (default: `production`)
- `MIRROR_HOST`, `MIRROR_PORT`: Dev server bind (default: `0.0.0.0:8080`)
- `MIRROR_BIND`: Gunicorn bind (default: `[::]:6100`)
- `MIRROR_WORKERS`: Gunicorn workers (default: CPU×2+1)
- `MIRROR_HTTP_TIMEOUT`: DC API timeout (default: 20s)
- `MIRROR_HEUNG_CACHE_TTL`: Heung gallery cache TTL (default: 3600s)
- `MIRROR_SECRET_KEY`: Flask secret key

Config classes in `app/config.py`: `DevelopmentConfig` (DEBUG=True), `ProductionConfig` (DEBUG=False)

## Deployment

Production: PM2 + Gunicorn
- `ecosystem.config.js`: PM2 config with file watching, auto-restart
- `gunicorn.conf.py`: Multi-worker with threading
- GitHub Actions auto-deploy on push to main
- PM2 cwd: `/home/ubuntu/mirror` (differs from repo path `/home/ubuntu/workspace/mirror`)

## Key Patterns

1. **Async scraping**: All DCinside calls use aiohttp for concurrency
2. **Multi-level caching**: Latest ID, related posts, author codes with TTL
3. **Smart related posts**: Probes pages around current post ID for context-relevant posts
4. **Media proxying**: Server-side image proxy handles DCinside referrer restrictions
5. **Cookie-based recent galleries**: Client tracking with server deduplication
