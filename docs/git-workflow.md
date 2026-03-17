---
description: Git 작업 규칙
---

이 워크플로의 기준 문서는 `docs/GIT_WORKFLOW_RULES.md` 이다.

## 핵심 규칙

1. **main에서 직접 작업하지 않는다.**
2. 작업을 시작하기 전에 feature 브랜치를 먼저 만든다.
3. 커밋 메시지는 `<type>: <한국어 설명>` 형식을 사용한다.
4. PR 제목도 같은 형식을 사용한다.
5. 기본 머지 방식은 `squash merge`다.

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
작업이 끝나면 사용자에게 PR/머지 여부를 물어본다. 직접 main에 머지하지 않는다.
