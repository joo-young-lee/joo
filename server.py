#!/usr/bin/env python3
"""
이직 리서치 어시스턴트 — 로컬 API 서버
브라우저(hr_assistant.html)에서 Claude API를 직접 호출하기 위한 경량 프록시
"""

import json, os, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

PORT = 7432
BASE = Path(__file__).parent

# ── Agent system prompts ────────────────────────────────────────────────────
AGENTS = {
    "jd_analysis": {
        "name": "🔍 JD 분석",
        "system": """당신은 이직 리서치 에이전트입니다.
범위는 채용공고 분석과 지원 판단 보조로 제한됩니다. 이력서/면접/연봉 코칭으로 확장하지 마세요.
사실, 해석, 추정을 구분하고 확인되지 않은 내용은 반드시 '확인 필요'로 표시하세요.

반드시 아래 형식으로 답하세요:
# 한줄 결론
# 회사/직무 핵심 요약
# 경력직 핏 분석
# 지원 우선순위 판단
# 리스크 및 확인 필요 항목
# 다음 액션

분석 기준:
- Must-have / Nice-to-have 분리
- 역할 범위와 기대 오너십
- 반복 키워드 기반 실제 우선순위
- 레드플래그와 모호한 표현
- 지원 전 확인할 질문과 자료"""
    },
    "matching": {
        "name": "🎯 나 vs JD 매칭",
        "system": """당신은 이직 리서치 에이전트입니다.
범위는 JD와 후보자 경력의 핏 판단입니다. 이력서 작성이나 면접 답변 작성으로 넘어가지 마세요.
사실, 해석, 추정을 구분하고 확인되지 않은 내용은 반드시 '확인 필요'로 표시하세요.

반드시 아래 형식으로 답하세요:
# 한줄 결론
# 회사/직무 핵심 요약
# 경력직 핏 분석
# 지원 우선순위 판단
# 리스크 및 확인 필요 항목
# 다음 액션

판정은 반드시 `적극 지원 / 조건부 지원 / 보류 또는 제외` 중 하나로 명시하세요.
3-5개의 핵심 근거와 구체적인 다음 액션을 포함하세요."""
    },
    "research": {
        "name": "🏢 종합 리서치",
        "system": """당신은 회사/직무 리서치 에이전트입니다.
외부 사실은 최신 여부를 확정하지 말고, 검증되지 않은 내용은 '확인 필요'로 표시하세요.
지원 판단에 필요한 정보만 간결하게 정리하고 범위를 벗어난 일반 커리어 코칭은 하지 마세요.

반드시 아래 형식으로 답하세요:
# 한줄 결론
# 회사/직무 핵심 요약
# 경력직 핏 분석
# 지원 우선순위 판단
# 리스크 및 확인 필요 항목
# 다음 액션

포함할 항목:
- 비즈니스 모델과 핵심 수익원
- 최근 전략 변화 또는 주요 이슈: 확인 필요 여부 명시
- 조직 문화 및 일하는 방식: 근거 수준 표시
- 경쟁 구도와 산업 트렌드
- 해당 포지션의 의미와 지원 전 확인 질문"""
    },
}

# ── HTTP Handler ────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 로그 억제

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/agents":
            data = {k: {"name": v["name"]} for k, v in AGENTS.items()}
            self._json(200, data)
        elif self.path == "/health":
            self._json(200, {"status": "ok", "port": PORT})
        elif self.path == "/" or self.path == "/index.html":
            html = BASE / "hr_assistant.html"
            if html.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self._cors()
                self.end_headers()
                self.wfile.write(html.read_bytes())
            else:
                self._json(404, {"error": "hr_assistant.html not found"})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/run", "/ocr"):
            self._json(404, {"error": "not found"}); return

        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        # ── /ocr: 이미지 → 텍스트 추출 ──────────────────────────────────────
        if self.path == "/ocr":
            api_key      = body.get("api_key", "")
            image_base64 = body.get("image_base64", "")
            media_type   = body.get("media_type", "image/jpeg")
            hint         = body.get("hint", "general")   # "jd" | "resume" | "general"
            if not api_key:
                self._json(400, {"error": "API 키가 없습니다"}); return

            prompts = {
                "jd": (
                    "이 이미지는 채용공고(JD)입니다. "
                    "직무명, 자격요건, 우대사항, 담당업무, 회사소개 등 모든 텍스트를 "
                    "원문 구조 그대로 정확하게 추출하세요. "
                    "항목 구분, 줄바꿈, 불릿포인트 등 원래 형식을 최대한 살려주세요. "
                    "추가 설명 없이 추출된 텍스트만 반환하세요."
                ),
                "resume": (
                    "이 이미지는 경력 문서입니다. "
                    "이름, 연락처, 경력사항, 학력, 스킬, 프로젝트 등 모든 텍스트를 "
                    "원문 구조 그대로 정확하게 추출하세요. "
                    "추가 설명 없이 추출된 텍스트만 반환하세요."
                ),
                "general": (
                    "이 이미지에 있는 모든 텍스트를 원문 구조와 줄바꿈을 살려서 정확하게 추출하세요. "
                    "추가 설명이나 코멘트 없이 텍스트만 반환하세요."
                ),
            }
            ocr_prompt = prompts.get(hint, prompts["general"])

            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)
                resp = client.messages.create(
                    model="claude-sonnet-4-6",  # Sonnet: 비전 앞확도 훨씬 높음
                    max_tokens=4000,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_base64}},
                            {"type": "text", "text": ocr_prompt}
                        ]
                    }]
                )
                self._json(200, {"result": resp.content[0].text})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return

        agent_id        = body.get("agent")
        user_msg        = body.get("message", "")
        api_key         = body.get("api_key", "")
        model           = body.get("model", "claude-haiku-4-5-20251001")
        memory_hints    = body.get("memory_hints", [])   # 계거 피드백 패턴
        system_override = body.get("system_override")    # 지정 왡접 지정 시스템 프롬프트

        if not api_key:
            self._json(400, {"error": "API 키가 없습니다"}); return

        # system_override는 agent 검증 없이 사용 가능 (프로필 추출 등)
        if system_override:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)
                resp = client.messages.create(
                    model=model, max_tokens=500,
                    system=system_override,
                    messages=[{"role": "user", "content": user_msg}],
                )
                self._json(200, {"result": resp.content[0].text})
            except Exception as e:
                self._json(500, {"error": str(e)})
            return

        if agent_id not in AGENTS:
            self._json(400, {"error": f"알 수 없는 에이전트: {agent_id}"}); return

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            agent  = AGENTS[agent_id]

            # 메모리 힌트를 시스템 프롬프트에 주입
            system_prompt = agent["system"]
            if memory_hints:
                likes    = [m["note"] for m in memory_hints if m.get("rating") == "good" and m.get("note")]
                dislikes = [m["note"] for m in memory_hints if m.get("rating") == "bad"  and m.get("note")]
                mem_block = "\n\n━━━━━━━━━━━━━━━━━━━━\n【이 사용자의 학습된 선호도】\n"
                if likes:
                    mem_block += "✅ 좋아했던 것:\n" + "\n".join(
                        f"- {l}" for l in likes[:4]) + "\n"
                if dislikes:
                    mem_block += "❌ 싫어했던 것:\n" + "\n".join(
                        f"- {d}" for d in dislikes[:4]) + "\n"
                mem_block += "→ 위 선호도를 반영해서 답변 스타일은 랐추주세요.\n━━━━━━━━━━━━━━━━━━━━"
                system_prompt = system_prompt + mem_block

            resp = client.messages.create(
                model=model,
                max_tokens=3000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}],
            )

            result = resp.content[0].text
            usage  = resp.usage
            # Cost estimate (haiku: $0.25/$1.25 per MTok, sonnet: $3/$15)
            rates = {
                "claude-haiku-4-5-20251001": (0.25, 1.25),
                "claude-sonnet-4-6": (3.0, 15.0),
            }
            in_r, out_r = rates.get(model, (3.0, 15.0))
            cost = (usage.input_tokens * in_r + usage.output_tokens * out_r) / 1_000_000

            self._json(200, {
                "result": result,
                "agent":  agent["name"],
                "tokens": {"in": usage.input_tokens, "out": usage.output_tokens},
                "cost_usd": round(cost, 5),
                "cost_krw": round(cost * 1380),
            })

        except Exception as e:
            self._json(500, {"error": str(e)})

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    try:
        import anthropic
    except ImportError:
        print("❌ anthropic 팠키지가 없⊵니다.")
        print("   pip3 install anthropic  을 ꆼ제 다했하세요.")
        sys.exit(1)

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    url    = f"http://localhost:{PORT}"

    print(f"""
┌─────────────────────────────────────────┐
│  🎯  이직 리서치 운시 스턴트 서버 시작   │
│                                         │
│  브라우저에서 열기:                         │
│  {url:<39}│
│                                         │
│  종료: Ctrl + C                             │
└─────────────────────────────────────────┘
""")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료.")

if __name__ == "__main__":
    main()
