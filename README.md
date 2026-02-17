# dcinside-web-mirror

Flask 기반 DCinside 미러 웹앱입니다.

## 현재 구조

- `app/`: 실제 서비스 코드
  - `app/routes.py`: 라우트/페이지 로직
  - `app/services/`: DC API 연동 및 데이터 처리
  - `app/templates/`, `app/static/`: 현재 사용 중인 UI
- `run.py`: 로컬 실행 엔트리포인트
- `wsgi.py`: Gunicorn/WSGI 엔트리포인트
- `legacy/`: 과거 구조 백업(현재 미사용)

## 로컬 실행

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

기본 주소: `http://127.0.0.1:8080`

환경변수:
- `MIRROR_HOST` (기본 `0.0.0.0`)
- `MIRROR_PORT` (기본 `8080`)
- `MIRROR_ENV` (`development` 또는 `production`)

## PM2 실행

```bash
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

`ecosystem.config.js`는 Gunicorn(`wsgi:app`) 기준으로 동작합니다.
