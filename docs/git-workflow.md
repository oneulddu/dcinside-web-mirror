---
description: Git 작업 규칙
---

이 워크플로의 기준 문서는 `docs/GIT_WORKFLOW_RULES.md` 이다.

## 핵심 규칙

1. 기본은 feature 브랜치에서 작업하고 PR로 `main`에 머지한다.
2. 작은 저위험 변경은 `main`에서 직접 작업할 수 있다.
3. 커밋 메시지는 `<type>: <한국어 설명>` 형식을 사용한다.
4. PR 제목도 같은 형식을 사용한다.
5. PR을 만들었다면 기본 머지 방식은 `squash merge`다.

### main 직접 작업
// turbo
```
git checkout main
git pull --ff-only origin main
git add -A
git commit -m "<type>: <한국어 설명>"
git push origin main
```

직접 작업 가능한 예:

- CSS 간격, 색상, 테두리, 정렬 같은 작은 시각 보정
- 문구, 오탈자, 주석, 문서 수정
- 동작 영향이 없거나 매우 낮은 단일 파일 수정

직접 작업하지 않는 예:

- 라우트, 데이터 처리, 스크래핑, 캐시, 보안, 배포 설정 변경
- 의존성 변경
- 테스트 기대값 변경
- 여러 파일에 걸친 리팩터링

### 브랜치 생성
// turbo
```
git checkout main
git pull origin main
git checkout -b feature/<작업요약-MMDD>
```

- 브랜치명 형식: `feature/<작업요약-MMDD>` (예: `feature/save-modal-design-0216`)
- 자동 충돌 회피 브랜치는 예외적으로 `feature/<작업요약-MMDD-HHMMSS>`를 사용할 수 있다.
- 작업요약은 영문 소문자, 하이픈 구분
- 날짜는 현재 날짜 기준 MMDD

### 작업 중 커밋 & 푸시
// turbo
```
git add -A
git commit -m "<type>: <한국어 설명>"
git push origin <브랜치명>
```

### PR 작성
// turbo
```
제목: <type>: <한국어 설명>
본문: 변경 요약 / 최근 커밋 / 변경 파일
```

### 작업 완료 후
작은 저위험 변경은 `main`에 바로 push해서 끝낼 수 있다. 그 외 변경은 PR을 만들고 사용자의 요청이나 리뷰 상태에 맞춰 머지한다.
