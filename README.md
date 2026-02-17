<div align="center">

# 🪞 DCinside Web Mirror

**Flask 기반 경량 DCinside 프록시 뷰어**

광고 없이 깔끔한 UI로 DCinside 갤러리를 탐색하세요.

[![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-2.0-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com)

</div>

---

## ✨ 주요 기능

| 기능 | 설명 |
|------|------|
| 🔥 **흥한 갤러리** | 실시간 인기 갤러리 순위 표시 (대흥갤·흥한갤) |
| 🔍 **갤러리 검색** | 이름 또는 Board ID로 갤러리 검색 |
| 📋 **게시판 뷰어** | 전체글·추천글 전환, 페이지네이션 지원 |
| 📖 **글 읽기** | 본문·이미지·댓글(대댓글 포함) 렌더링 |
| 🖼️ **미디어 프록시** | 이미지를 서버에서 직접 프록시하여 안정적으로 표시 |
| 🕐 **최근 방문** | 쿠키 기반 최근 방문 갤러리 기록 (최대 30개) |
| 🔗 **관련 게시글** | 현재 글 주변 게시글을 자동으로 불러오는 무한 탐색 |
| 🌙 **다크 모드** | 원클릭 라이트/다크 테마 전환 |
| 🛡️ **스팸 필터** | 댓글 스팸 자동 필터링 |

---

## 🏗️ 프로젝트 구조

```
mirror/
├── app/
│   ├── __init__.py          # Flask 앱 팩토리 (create_app)
│   ├── config.py            # Dev / Production 설정
│   ├── routes.py            # 라우트 & 비즈니스 로직
│   ├── services/
│   │   ├── dc_api.py        # DCinside 비동기 스크래핑 API
│   │   └── core.py          # 게시판 조회·글 읽기·관련글 로직
│   ├── templates/
│   │   ├── base.html        # 공통 레이아웃 (헤더·탭·다크모드)
│   │   ├── index.html       # 홈 — 흥한 갤러리 & 검색
│   │   ├── board.html       # 게시판 목록
│   │   ├── read.html        # 글 읽기 & 댓글
│   │   └── recent.html      # 최근 방문 갤러리
│   └── static/
│       ├── css/main.css     # 전체 스타일시트
│       └── javascript/
│           ├── read_state.js            # 다크모드 & UI 상태
│           ├── read_related_loader.js   # 관련 게시글 비동기 로더
│           └── comment_spam_filter.js   # 댓글 스팸 필터
├── run.py                   # 로컬 개발 서버 엔트리포인트
├── wsgi.py                  # Gunicorn/WSGI 엔트리포인트
├── gunicorn.conf.py         # Gunicorn 설정
├── ecosystem.config.js      # PM2 프로세스 매니저 설정
├── Makefile                 # 편의 명령어
├── requirements.txt         # Python 의존성
└── .env.example             # 환경변수 템플릿
```

---

## 🚀 시작하기

### 사전 요구사항

- **Python 3.8+**
- **pip**

### 설치 & 실행

```bash
# 1. 레포 클론
git clone https://github.com/oneulddu/dcinside-web-mirror.git
cd dcinside-web-mirror

# 2. 가상환경 설정
python -m venv .venv
source .venv/bin/activate

# 3. 의존성 설치
pip install -r requirements.txt

# 4. 환경변수 설정 (선택)
cp .env.example .env
# .env 파일을 편집하여 필요한 값 수정

# 5. 개발 서버 실행
python run.py
```

> 기본 접속 주소: **http://127.0.0.1:8080**

또는 `make` 사용:

```bash
make install    # 의존성 설치
make run        # 개발 서버 실행
make run-prod   # Gunicorn 프로덕션 실행
```

---

## ⚙️ 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `MIRROR_ENV` | `production` | `development` / `production` |
| `MIRROR_HOST` | `0.0.0.0` | 바인드 호스트 |
| `MIRROR_PORT` | `8080` | 바인드 포트 |
| `MIRROR_BIND` | `[::]:6100` | Gunicorn 바인드 주소 |
| `MIRROR_WORKERS` | `auto` | Gunicorn 워커 수 (CPU×2+1) |
| `MIRROR_THREADS` | `2` | 워커당 스레드 수 |
| `MIRROR_TIMEOUT` | `60` | 요청 타임아웃 (초) |
| `MIRROR_HTTP_TIMEOUT` | `20` | DC API 요청 타임아웃 (초) |
| `MIRROR_HEUNG_CACHE_TTL` | `3600` | 흥한 갤러리 캐시 TTL (초) |
| `MIRROR_MEDIA_CACHE_MAX_AGE` | `86400` | 미디어 프록시 캐시 TTL (초) |
| `MIRROR_RECENT_MAX_ITEMS` | `30` | 최근 방문 최대 저장 수 |
| `MIRROR_SECRET_KEY` | `change-me` | Flask 시크릿 키 |

---

## 🖥️ 프로덕션 배포

### PM2 + Gunicorn

```bash
# Gunicorn 직접 실행
gunicorn -c gunicorn.conf.py wsgi:app

# PM2로 프로세스 관리
pm2 start ecosystem.config.js
pm2 save
pm2 startup
```

`ecosystem.config.js`는 파일 변경 시 자동 재시작(watch)을 지원합니다.

---

## 🛠️ 기술 스택

| 영역 | 기술 |
|------|------|
| **백엔드** | Python · Flask · Gunicorn |
| **스크래핑** | aiohttp · lxml · BeautifulSoup4 |
| **프론트엔드** | Jinja2 · Vanilla JS · CSS |
| **배포** | PM2 · systemd |

## 🔗 출처 및 관련 프로젝트

이 프로젝트는 [mirusu400/dcinside-web-mirror](https://github.com/mirusu400/dcinside-web-mirror)의 코드를 기반으로 커스텀 및 개선된 버전입니다.

---

<div align="center">

**Made with ❤️ for a cleaner DCinside experience**

</div>
