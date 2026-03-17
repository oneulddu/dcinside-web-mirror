# 업스트림 Throttle 구현 완료

## 구현 내용

### Phase 1-3 완료 ✅

**신규 파일:**
- `app/services/upstream_throttle.py` (140줄)
- `tests/test_upstream_throttle.py` (40줄)

**수정 파일:**
- `app/services/dc_api.py` (+2줄 import, +15줄 throttle 로직)
- `app/routes.py` (+2줄 import, +1줄 throttle 호출)
- `requirements.txt` (+2줄)
- `.env.example` (+7줄)
- `README.md` (+15줄)

**총 변경량:**
- 신규 코드: ~180줄
- 기존 코드 수정: ~20줄

## 테스트 결과

```bash
$ python3 -m pytest tests/test_upstream_throttle.py -v
============================== 4 passed in 0.45s ===============================
```

모든 테스트 통과:
- ✅ throttle 비활성화 시 즉시 실행
- ✅ 최소 간격 적용 (150ms)
- ✅ rate limit backoff 동작
- ✅ rate limit 응답 감지

## 적용된 기능

### 1. 요청 간 최소 간격
- 업스트림 요청 시작 시점을 150ms 간격으로 제어
- jitter 50ms 추가로 기계적 패턴 회피

### 2. Rate Limit 자동 감지
- HTTP 429 상태 코드
- 응답 본문의 "Too Many Requests", "너무 많은 요청", "penalty-box" 문자열
- `Retry-After` 헤더 반영

### 3. 자동 Backoff
- 첫 감지: 5초 휴지
- 연속 감지: 5s → 10s → 15s 점증
- 최대 15초로 제한

### 4. 공통 진입점 활용
- `dc_api.py`의 `__fetch_parsed_from_urls()` 한 곳만 수정
- `board()`, `document()`, `comments()` 등 모든 메서드에 자동 적용

## 미완료 항목 (선택 사항)

### Phase 4: Secondary Fetch Budget
- `core.py`의 `author_code` 보강 요청 수 제한
- 현재는 캐시(TTL 600초)로 충분히 제어됨
- 필요 시 추가 구현 가능

### 추가 POST 요청 래핑
- `__gallery_miner_from_web()` (line 265)
- `__access()` (line 1067)
- 빈도가 낮아 우선순위 낮음

## 사용 방법

### 기본 사용 (이미 활성화됨)
```bash
# .env에 설정 (기본값 사용)
MIRROR_UPSTREAM_THROTTLE_ENABLED=true
```

### 디버깅
```bash
# 로그 활성화
MIRROR_UPSTREAM_LOG_EVENTS=true

# 서버 실행
make run

# 로그 확인
[throttle] waiting 0.15s
[throttle] rate limit detected, backoff 5000ms
```

### 비활성화 (롤백)
```bash
MIRROR_UPSTREAM_THROTTLE_ENABLED=false
```

## 예상 효과

### Before
- 평균 응답 시간: 300ms
- 429 에러: 가끔 발생
- 동시 업스트림 요청: 제한 없음

### After
- 평균 응답 시간: 400~500ms (+100~200ms)
- 429 에러: 거의 없음
- 동시 업스트림 요청: 프로세스당 제어됨

## 다음 단계

1. **프로덕션 배포**
   ```bash
   pm2 restart dc-mirror
   pm2 logs dc-mirror
   ```

2. **모니터링**
   - 응답 시간 변화 확인
   - 429 에러 빈도 확인
   - 필요 시 `MIN_INTERVAL_MS` 튜닝

3. **선택적 개선**
   - Phase 4 구현 (author_code budget)
   - POST 요청 래핑
   - Redis 기반 분산 throttle

## 참고 문서

- `docs/throttle/README.md` - 전체 개요
- `docs/throttle/05-CODE-ANALYSIS.md` - 코드 분석 결과
- `docs/throttle/04-OPERATIONS.md` - 운영 가이드
