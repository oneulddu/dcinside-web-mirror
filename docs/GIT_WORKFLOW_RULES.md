# Git Workflow Rules

이 문서는 이 저장소에서 실제로 맞춰서 쓰는 Git 작업 규칙을 정리한다.

## 0. 사전 설정

- Git 자동화 훅은 저장소에 제공될 때만 설치한다.
- 현재 저장소 기준으로는 `.githooks` 디렉터리가 없으므로 훅 설치를 전제로 작업하지 않는다.
- 훅이 추가된 경우 `commit-msg`는 커밋 제목 형식을 검사하고, `pre-push`는 큰 변경의 무분별한 `main` 직접 push를 막는 용도로 둔다.
- GitHub Actions는 PR과 `main` push에서 테스트와 배포를 실행한다.

설치:

```bash
bash ./scripts/install-git-hooks.sh
```

## 1. 브랜치 규칙

- 기본은 feature 브랜치에서 작업한 뒤 PR로 `main`에 머지한다.
- 다만 작은 저위험 변경은 `main`에서 직접 작업할 수 있다.
- feature 브랜치를 만들 때는 `feature/<작업요약-MMDD>` 형식을 사용한다.
- 작업요약은 영문 소문자와 하이픈만 사용한다.
- 자동 충돌 회피 브랜치는 예외적으로 `feature/<작업요약-MMDD-HHMMSS>` 형식을 사용할 수 있다.

### `main` 직접 작업이 가능한 경우

아래 조건을 모두 만족하면 `main`에서 직접 수정, 커밋, push할 수 있다.

- CSS 간격, 색상, 테두리, 정렬처럼 화면의 작은 시각 보정이다.
- 문구, 오탈자, 주석, 문서처럼 동작 영향이 없거나 매우 낮다.
- 라우팅, 데이터 처리, 스크래핑, 캐시, 보안, 배포 설정, 의존성, 테스트 기대값을 건드리지 않는다.
- 변경 파일과 diff를 보고 저위험이라고 명확히 설명할 수 있다.

`main` 직접 작업 전에는 항상 최신 상태를 받는다.

```bash
git checkout main
git pull --ff-only origin main
```

`main` 직접 작업 후에는 필요한 최소 검증을 실행한다. CSS 미세 조정은 브라우저 확인이나 `git diff --check` 정도로 충분할 수 있고, 동작 영향이 있으면 `make test`를 실행한다.

### 브랜치가 필요한 경우

아래 변경은 작은 수정처럼 보여도 브랜치를 만든다.

- 라우트, 템플릿 구조, API 응답, 데이터 모델 변경
- 스크래핑, 캐시, 미디어 프록시, 보안 관련 변경
- 배포 설정, GitHub Actions, 의존성 변경
- 테스트 기대값 변경
- 여러 파일에 걸친 리팩터링이나 되돌리기 어려운 변경

예시:

```bash
git checkout main
git pull --ff-only origin main
git checkout -b feature/rembg-background-removal-0310
```

## 2. 커밋 메시지 규칙

- 형식은 `<type>: <한국어 설명>`을 사용한다.
- 설명은 한국어로 쓴다.
- 여러 의미를 섞지 말고 커밋 하나에 하나의 목적만 담는다.

예시:

```text
feat: 비밀번호 보호 배경 제거 도구 추가
fix: 비밀번호 오류 시 대기열 중단
fix: 처리 중 대기열 상태 고정
refactor: index 페이지 구조 정리
ci: 인벤토리 배포용 Node 인터프리터 고정
```

권장 type:

- `feat`: 기능 추가
- `fix`: 버그 수정
- `refactor`: 동작 변경 없는 구조 개선
- `docs`: 문서 수정
- `ci`: 배포, GitHub Actions, 훅, 자동화 수정
- `chore`: 잡일성 설정 변경

### 커밋 설명 공유 형식

- 커밋 제목은 짧게 유지한다.
- 대신 사람에게 작업 내용을 전달할 때는 본문 bullet을 같이 붙여서 보낸다.
- 즉, 히스토리용 제목과 설명용 본문을 분리해서 사용한다.

예시:

```text
refactor: index.html에서 CSS/JS를 별도 파일로 분리
- index.html (5,119줄) → index.html (263줄) + index.css (2,495줄) + app.js (2,358줄)
- 유지보수성 향상, diff가 깔끔해지고 브라우저 캐싱 활용 가능
- 기능 변경 없음 (pure refactor)
```

## 3. 작성자 규칙

- Git 작성자는 `oneulddu` 계정을 사용한다.
- 기본 이메일은 GitHub noreply 주소를 사용한다.

기준값:

```bash
git config --global user.name "oneulddu"
git config --global user.email "84663820+oneulddu@users.noreply.github.com"
```

주의:

- 어떤 저장소에서 `git config --local user.name` 또는 `git config --local user.email`을 따로 설정하면 전역값보다 우선한다.
- 커밋 작성자가 이상하면 전역 설정뿐 아니라 현재 저장소의 로컬 설정도 같이 확인한다.

## 4. PR 제목 규칙

- PR 제목도 커밋과 같은 형식을 사용한다.
- 즉, `<type>: <한국어 설명>` 형태로 작성한다.
- PR 제목은 squash merge 시 main에 들어갈 최종 커밋 제목의 기준이 된다.

예시:

```text
feat: 비밀번호 보호 배경 제거 도구 추가
refactor: index.html에서 CSS/JS를 별도 파일로 분리
```

## 5. PR 작업 규칙

- 같은 feature 브랜치에 커밋을 추가하고 push하면 기존 PR이 자동 갱신된다.
- 리뷰 코멘트 대응은 가능하면 별도 커밋으로 남긴다.
- 해결한 리뷰 코멘트는 PR 스레드도 `resolved` 상태로 정리한다.
- 자동 리뷰나 봇 코멘트의 권장사항을 반영한 경우, 어떤 커밋에서 해결했는지 PR 코멘트로 짧게 남긴다.
- 즉, `코드 수정 -> 커밋/푸시 -> 스레드 resolve -> 해결 요약 코멘트` 순서로 정리한다.

### PR 본문 형식

- PR 본문은 아래 순서를 기본으로 사용한다.
- 제목은 짧게, 본문은 바로 스캔 가능한 형태로 쓴다.

예시:

```text
변경 요약
배경 제거 페이지 추가, iLoveAPI 서버 프록시 연결, 다중 파일 처리 개선

최근 커밋
56455a8 refactor: index.html에서 CSS/JS를 별도 파일로 분리

변경 파일
.githooks/post-commit
.githooks/pre-push
public/app.js
public/index.css
public/index.html
```

## 6. 머지 규칙

- 기본은 PR 리뷰 후 `main`으로 머지한다.
- 작은 저위험 변경은 `main`에 직접 push할 수 있다.
- 머지는 가능하면 `squash merge`를 사용한다.
- 최종 main 커밋 제목은 아래 형식을 맞춘다.

예시:

```text
feat: 비밀번호 보호 배경 제거 도구 추가 (#9)
refactor: index.html에서 CSS/JS를 별도 파일로 분리 (#8)
```

## 7. 머지 후 정리

- 머지 후에는 feature 브랜치를 정리한다.
- 원격 브랜치와 로컬 브랜치를 모두 삭제한다.
- 임시 backup 브랜치나 rewrite 브랜치가 있으면 같이 정리한다.

예시:

```bash
git checkout main
git pull --ff-only origin main
git branch -D feature/<브랜치명>
git push origin --delete feature/<브랜치명>
```

## 8. 예외 상황

- 작은 저위험 변경이 아니어도 긴급 hotfix라면 `main` 직접 push를 고려할 수 있다.
- 이 경우에는 왜 예외 처리를 했는지 남겨둔다.
- 히스토리 재작성이나 force push는 필요 범위를 최소화하고, 가능하면 backup 브랜치를 먼저 만든다.

훅이나 보호 규칙이 `main` push를 막고 있고 예외 처리가 정말 필요할 때:

```bash
export ALLOW_MAIN_PUSH=1
git push origin main
unset ALLOW_MAIN_PUSH
```

## 9. 자동화 훅 참고

- `post-commit` 훅은 Codex 자동화를 실행할 수 있다.
- 환경에 따라 `node --check` 같은 검증이 정책에 막힐 수 있다.
- 그런 경우 아래 옵션을 사용한다.

한 번만 unsafe 실행:

```bash
bash ./scripts/antigravity-codex-auto.sh --unsafe-no-sandbox
```

현재 터미널에서 hook-triggered 실행을 unsafe 허용:

```bash
export AG_CDX_UNSAFE=1
```

정책 차단이 감지되면 한 번 더 unsafe 재시도:

```bash
export AG_CDX_AUTO_UNSAFE_RETRY=1
```
