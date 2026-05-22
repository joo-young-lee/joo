#!/bin/bash
cd "$(dirname "$0")"

# 가상환경 활성화
if [ -f venv/bin/activate ]; then
  source venv/bin/activate
else
  echo "venv 없음. 설치 먼저: python3 -m venv venv && pip install -r requirements.txt"
  read -p "엔터를 누르면 닫힙니다..."
  exit 1
fi

# 서버 시작
uvicorn app:app --port 8000 &
SERVER_PID=$!

sleep 2

# 브라우저 열기
open http://localhost:8000

echo "서버 실행 중... 이 창을 닫으면 서버도 꺼집니다."
wait $SERVER_PID
