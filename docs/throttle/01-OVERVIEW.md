# DCinside Web Mirror 업스트림 요청 Throttle 기능 - 개요

## 목적

DCinside 업스트림으로 짧은 시간에 요청이 몰리는 상황을 줄이기 위해 **요청 간 최소 간격**과 **레이트리밋 감지 후 backoff**를 추가한다.

핵심 목표:
1. 업스트림 요청 폭주 억제
2. 429/"너무 많은 요청" 응답 시 자동 재시도
3. 페이지 무결성 유지하면서 부가 정보는 필요 시 생략

## 현재 문제

### 1) 요청 속도 제어 없음

`app/services/dc_api.py`와 `app/routes.py`에서 업스트림 호출 시:
- `Semaphore` 없음
- `sleep(...)` 없음
- `429` 대응 없음
- `retry`/`backoff` 없음

### 2) N+1 패턴 존재

- `/board` → 목록 HTML 가져온 뒤 `author_code` 비어있으면 `_fill_missing_author_code(...)`에서 추가 호출
- `/read/related` → 여러 페이지 탐색 후 각 항목마다 다시 `_fill_missing_author_code(...)` 호출 가능

**1개 화면 응답 = 다수 업스트림 요청**

### 3) 동기/비동기 혼재

- `dc_api.py`: `aiohttp.ClientSession` (비동기)
- `routes.py`: `requests.get(...)` (동기)

두 경로가 같은 throttle 정책을 공유해야 함.

### 4) 기존 완화 장치의 한계

- 캐시: `_LATEST_ID_CACHE`, `_RELATED_CACHE`, `_AUTHOR_CODE_CACHE` (TTL 기반)
- 클라이언트: `read_related_loader.js`의 `requestIdleCallback(...)`

하지만 **캐시 미스 시 burst 제어는 없음**.

## 왜 필요한가

- 게시판 목록 진입 시 여러 author_code 보강 요청
- 관련 게시글 로딩 시 페이지 탐색 + 보강 요청
- 흥한 갤러리/검색 호출
- 다중 워커 환경에서 동시성 증가 = 업스트림 burst 증가

README의 Gunicorn 설정(`MIRROR_WORKERS`)을 보면 실제 배포 시 워커가 여러 개이므로, 단순 캐시만으로는 부족하다.

## 해결 방향

1. **서버-업스트림 간 요청 속도 제어** (프런트 지연 UI 아님)
2. **핵심 데이터 우선**, 부가 데이터는 필요 시 축소
3. **환경변수 기반 설정**
4. **프로세스 단위 구현** (다중 워커 글로벌 레이트리미터는 2차 과제)

## 성공 기준

- `dc_api.py`의 업스트림 GET/POST가 공통 throttle 래퍼를 탄다
- 홈 흥한 ���러리/갤러리 검색 sync 호출도 동일 정책 공유
- 429 또는 rate-limit 문구 감지 시 5초 이상 backoff
- `/board`와 `/read/related`에서 author_code 보강 요청 수 제한
- `.env.example`과 README에 새 설정 문서화
- `MIRROR_UPSTREAM_THROTTLE_ENABLED=false` 시 기존 동작으로 복귀
