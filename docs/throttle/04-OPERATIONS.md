# DCinside Web Mirror 업스트림 Throttle - 운영 가이드

## 배포

### 환경변수 설정

`.env` 파일에 추가:
```bash
# Throttle 기본 설정
MIRROR_UPSTREAM_THROTTLE_ENABLED=true
MIRROR_UPSTREAM_MIN_INTERVAL_MS=150
MIRROR_UPSTREAM_MAX_CONCURRENCY=2
MIRROR_UPSTREAM_JITTER_MS=50
MIRROR_UPSTREAM_RATE_LIMIT_BACKOFF_MS=5000
MIRROR_UPSTREAM_RATE_LIMIT_MAX_BACKOFF_MS=15000

# Budget 설정
MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_BOARD_PAGE=8
MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_RELATED_LOAD=4

# 디버깅 (개발 환경에서만)
MIRROR_UPSTREAM_LOG_EVENTS=false
```

### PM2 재시작

```bash
pm2 restart dc-mirror
pm2 logs dc-mirror --lines 100
```

### 기능 비활성화 (롤백)

문제 발생 시 즉시 비활성화:
```bash
# .env 수정
MIRROR_UPSTREAM_THROTTLE_ENABLED=false

# 재시작
pm2 restart dc-mirror
```

---

## 모니터링

### 로그 확인

개발 환경에서 throttle 이벤트 로깅:
```bash
MIRROR_UPSTREAM_LOG_EVENTS=true make run
```

로그 예시:
```
[throttle] waiting 0.12s
[throttle] rate limit detected, backoff 5000ms
[throttle] blocked, waiting 4.87s
```

### 관측 지표

**정상 동작 징후:**
- 응답 시간이 약간 증가 (평균 +100~200ms)
- 429 에러 빈도 감소
- author_code 누락이 가끔 발생 (budget 소진 시)

**문제 징후:**
- 응답 시간이 과도하게 증가 (>2초)
- rate limit backoff가 빈번하게 발생
- 페이지가 비정상적으로 느림

---

## 튜닝 가이드

### 시나리오 1: 응답이 너무 느림

**원인:** 간격이 너무 크거나 concurrency가 너무 낮음

**해결:**
```bash
MIRROR_UPSTREAM_MIN_INTERVAL_MS=100  # 150 → 100
MIRROR_UPSTREAM_MAX_CONCURRENCY=3    # 2 → 3
```

### 시나리오 2: 여전히 429 에러 발생

**원인:** 간격이 너무 짧거나 budget이 너무 높음

**해결:**
```bash
MIRROR_UPSTREAM_MIN_INTERVAL_MS=200  # 150 → 200
MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_BOARD_PAGE=4  # 8 → 4
```

### 시나리오 3: author_code가 너무 자주 누락

**원인:** budget이 너무 낮음

**해결:**
```bash
MIRROR_AUTHOR_CODE_FETCH_BUDGET_PER_BOARD_PAGE=12  # 8 → 12
```

### 시나리오 4: 다중 워커 환경에서 여전히 burst

**원인:** 프로세스별 throttle이라 워커 간 조율 안 됨

**해결 (2차 확장):**
- Redis 기반 shared state 도입
- 또는 워커 수 감소

---

## 트러블슈팅

### Q1: throttle이 적용되지 않는 것 같아요

**확인 사항:**
1. `MIRROR_UPSTREAM_THROTTLE_ENABLED=true` 설정 확인
2. PM2 재시작 했는지 확인
3. `MIRROR_UPSTREAM_LOG_EVENTS=true`로 로그 확인

### Q2: 페이지가 너무 느려졌어요

**임시 조치:**
```bash
MIRROR_UPSTREAM_THROTTLE_ENABLED=false
pm2 restart dc-mirror
```

**근본 해결:**
- 간격/concurrency 튜닝
- 캐시 TTL 증가 고려

### Q3: author_code가 모두 사라졌어요

**원인:** rate-limit 상태에서 budget이 0으로 설정됨

**확인:**
```bash
pm2 logs dc-mirror | grep "rate limit"
```

**해결:** 잠시 기다리면 backoff 해제 후 정상화

### Q4: 특정 경로만 throttle 제외하고 싶어요

**예: `/media` 프록시 제외**

현재는 `MIRROR_MEDIA_THROTTLE_ENABLED=false`가 기본값이므로 이미 제외됨.

다른 경로 제외는 코드 수정 필요 (2차 확장).

---

## 성능 영향 예상

### Before (throttle 없음)
- 평균 응답 시간: 300ms
- 429 에러: 가끔 발생
- 동시 업스트림 요청: 제한 없음

### After (throttle 적용)
- 평균 응답 시간: 400~500ms (+100~200ms)
- 429 에러: 거의 없음
- 동시 업스트림 요청: 프로세스당 2개

**트레이드오프:** 약간의 응답 시간 증가를 감수하고 안정성 확보

---

## 후속 확장 아이디어

### 1. 관측성 강화
- Prometheus metrics 추가
- 최근 1분 rate-limit 감지 횟수
- 현재 blocked 상태 여부
- secondary fetch skip 횟수

### 2. 분산 throttle
- Redis 기반 shared state
- 여러 worker/인스턴스 간 통합 한도

### 3. 동적 정책
- 최근 실패가 많으면 자동으로 budget 축소
- 한동안 안정적이면 원복

### 4. 관련글 로더 재시도
- 서버가 throttle 상태일 때 `/read/related` 응답에 `deferred: true`
- 프런트가 2~3초 뒤 1회 재시도
