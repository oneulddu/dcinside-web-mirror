# dcinside-web-mirror
## example : http://dcweb.tech
![설명](https://raw.githubusercontent.com/mirusu400/dcinside-web-mirror/main/doc/overview.png)

### 보안적 사유로 디시 URL이 막힌 곳, 디시가 느려서 제대로 작동하지 않는 곳, 트래픽을 적게 사용하고 싶은 사람들을 위한 디시 마이크로 미러 웹서버

`Flask` + `dc_api` 를 이용한 비공식 디시 마이크로 미러 웹서버입니다.

모든 이미지를 포함한 본문, 디시콘이 디시 경로를 거치지 않고 로드되기 때문에, 보안적 사유로 디시에 접근하지 못하는 사람들이 쉽게 게시물을 볼 수 있습니다.

또한, 꼭 필요한 정보들만을 전송하기 때문에 매우 가볍고, 속도가 빠르며, 트래픽을 적게 소모합니다.

** 이 미러 웹서버는 글을 저장하거나 백업해놓지 않습니다! **

# Functions
* 갤러리 목록 찾기
* 갤러리 일람(게시글 목록, 댓글, 좋아요수, 작성자 확인 등)
* 개념글 일람
* 글 일람

# Installation
```
git clone https://github.com/mirusu400/dcinside-web-mirror
pip install -r requirements.txt
python index.py
```
> 테스트 주소 : http://127.0.0.1:8080

# 불가능한 것
* 보이스 리플 가져오기 ==> 동적으로 보플을 재생해야만 원본 링크가 나오기 때문에 가져오기가 어렵습니다

# TODO
* Docker화
* 마이너 갤러리 목록 불러오기

## PM2 실행 (watch + 6100 포트)
```bash
# 1) pm2 설치 (전역)
npm install -g pm2

# 2) 앱 실행 (watch 모드)
pm2 start ecosystem.config.js

# 3) 재부팅 후 자동 실행 등록
pm2 startup
pm2 save
```

기본 바인드는 `0.0.0.0:6100`입니다.
