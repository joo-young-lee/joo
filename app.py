import os
import asyncio
from typing import Optional, List
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="AI 연봉협상 전략 도우미")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")


class UserProfile(BaseModel):
    role: str
    experience: int
    current_salary: int
    desired_salary: int
    company: Optional[str] = ""
    industry: Optional[str] = ""
    location: Optional[str] = "서울"


class ChatMessage(BaseModel):
    role: str
    content: str


class SimulateRequest(BaseModel):
    message: str
    history: List[ChatMessage]
    profile: UserProfile
    ai_model: str


class CompareRequest(BaseModel):
    responses: dict
    profile: UserProfile
    mode: str = "analysis"


class DebateRequest(BaseModel):
    topic: str
    profile: UserProfile
    rounds: int = 2


# --- AI call helpers ---

async def call_claude(prompt: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY 미설정")
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


async def call_gpt(prompt: str) -> str:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY 미설정")
    import openai
    client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
    response = await client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


async def call_gemini(prompt: str) -> str:
    if not GOOGLE_API_KEY:
        raise ValueError("GOOGLE_API_KEY 미설정")

    def _sync() -> str:
        import google.generativeai as genai
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        return model.generate_content(prompt).text

    return await asyncio.to_thread(_sync)


async def call_claude_chat(system: str, history: List[ChatMessage], message: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY 미설정")
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
    messages = [{"role": h.role, "content": h.content} for h in history]
    messages.append({"role": "user", "content": message})
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=system,
        messages=messages,
    )
    return response.content[0].text


async def call_gpt_chat(system: str, history: List[ChatMessage], message: str) -> str:
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY 미설정")
    import openai
    client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY)
    messages = [{"role": "system", "content": system}]
    for h in history:
        messages.append({"role": h.role, "content": h.content})
    messages.append({"role": "user", "content": message})
    response = await client.chat.completions.create(
        model="gpt-4o",
        max_tokens=600,
        messages=messages,
    )
    return response.choices[0].message.content


async def call_gemini_chat(system: str, history: List[ChatMessage], message: str) -> str:
    if not GOOGLE_API_KEY:
        raise ValueError("GOOGLE_API_KEY 미설정")

    def _sync() -> str:
        import google.generativeai as genai
        genai.configure(api_key=GOOGLE_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash", system_instruction=system)
        gemini_history = [
            {"role": "user" if h.role == "user" else "model", "parts": [h.content]}
            for h in history
        ]
        chat = model.start_chat(history=gemini_history)
        return chat.send_message(message).text

    return await asyncio.to_thread(_sync)


def _fmt(r) -> str:
    return str(r) if isinstance(r, Exception) else r


# --- Prompts ---

def analysis_prompt(p: UserProfile) -> str:
    return f"""당신은 한국 취업/이직 시장의 연봉 전문 컨설턴트입니다.

[지원자 프로필]
직군: {p.role} | 경력: {p.experience}년 | 현재 연봉: {p.current_salary:,}만원 | 희망 연봉: {p.desired_salary:,}만원
회사: {p.company or '미기입'} | 산업: {p.industry or '미기입'} | 지역: {p.location}

아래 항목을 분석해주세요:

## 1. 시장 연봉 범위
해당 직군/경력의 시장 연봉 (하위 25% / 중앙값 / 상위 25%)을 구체적 수치로 제시하세요.

## 2. 현재 포지셔닝
현재 연봉이 시장 대비 어느 위치인지 평가하세요.

## 3. 희망 연봉 현실성
희망 연봉 {p.desired_salary:,}만원 달성 가능성을 평가하세요.

## 4. 최적 협상 목표
- 협상 시작가 (앵커링용)
- 현실적 목표 연봉
- 최소 수락 가능 연봉

## 5. 핵심 전략 포인트
이 상황에서 강조해야 할 3가지 포인트를 제시하세요."""


def script_prompt(p: UserProfile) -> str:
    return f"""당신은 연봉협상 전문 코치입니다.

[협상 상황]
직군: {p.role} | 경력: {p.experience}년 | 현재 연봉: {p.current_salary:,}만원 → 목표: {p.desired_salary:,}만원
회사: {p.company or '목표 회사'} | 산업: {p.industry or '미기입'}

실전에서 바로 사용 가능한 협상 스크립트를 작성해주세요:

## 1. 오프닝 멘트
협상을 자연스럽게 시작하는 말 (연봉 주제를 꺼내는 방법)

## 2. 가치 어필 (3가지)
내 경력/역량으로 이 연봉을 받아야 하는 구체적 이유

## 3. 연봉 제시
희망 연봉을 제시하는 방법과 멘트 (숫자 포함)

## 4. 거절 대응
회사가 낮은 금액을 제시할 때 쓸 수 있는 대응 멘트 2가지

## 5. 마무리
합의를 이끌어내는 클로징 멘트

자연스러운 한국어 구어체로 작성해주세요."""


# --- API Routes ---

@app.get("/api/health")
async def health():
    return {
        "apis": {
            "claude": bool(ANTHROPIC_API_KEY),
            "gpt": bool(OPENAI_API_KEY),
            "gemini": bool(GOOGLE_API_KEY),
        }
    }


@app.post("/api/analyze")
async def analyze(profile: UserProfile):
    prompt = analysis_prompt(profile)
    results = await asyncio.gather(
        call_claude(prompt), call_gpt(prompt), call_gemini(prompt),
        return_exceptions=True,
    )
    return {"claude": _fmt(results[0]), "gpt": _fmt(results[1]), "gemini": _fmt(results[2])}


@app.post("/api/script")
async def script(profile: UserProfile):
    prompt = script_prompt(profile)
    results = await asyncio.gather(
        call_claude(prompt), call_gpt(prompt), call_gemini(prompt),
        return_exceptions=True,
    )
    return {"claude": _fmt(results[0]), "gpt": _fmt(results[1]), "gemini": _fmt(results[2])}


@app.post("/api/compare")
async def compare(req: CompareRequest):
    p = req.profile
    label = "시장 분석" if req.mode == "analysis" else "협상 스크립트"
    prompt = f"""아래는 동일한 연봉협상 상황({p.role}, {p.experience}년, {p.current_salary:,}→{p.desired_salary:,}만원)에 대한 3개 AI의 {label} 답변입니다.

**[Claude]**
{req.responses.get('claude', '응답 없음')}

**[GPT-4]**
{req.responses.get('gpt', '응답 없음')}

**[Gemini]**
{req.responses.get('gemini', '응답 없음')}

---

위 3가지 답변을 비교 분석하여 다음을 정리해주세요:

## ✅ 공통 핵심 포인트
3개 AI가 공통으로 강조하는 중요 포인트

## 🔀 AI별 차이점 & 독특한 시각
각 AI의 독특한 접근법이나 다른 시각

## 🎯 최종 종합 추천
3개 의견을 종합한 최적의 전략 요약 (실행 우선순위 포함)"""

    errors = []
    for fn in [call_claude, call_gpt, call_gemini]:
        try:
            result = await fn(prompt)
            return {"synthesis": result}
        except Exception as e:
            errors.append(str(e))
    return {"synthesis": "⚠️ 모든 API 키가 설정되지 않아 종합 분석을 실행할 수 없습니다.\n" + "\n".join(errors)}


@app.post("/api/simulate")
async def simulate(data: SimulateRequest):
    system = f"""당신은 {data.profile.company or '회사'} 인사담당자(HR Manager)입니다.
{data.profile.role} 포지션 지원자({data.profile.experience}년 경력)와 연봉 협상 중입니다.
- 회사 제안: {data.profile.current_salary:,}만원
- 지원자 희망: {data.profile.desired_salary:,}만원

역할 지침:
- 처음엔 예산 제약을 이유로 조금 저항하세요
- 지원자가 설득력 있는 근거를 제시하면 일부 수용하세요
- 연봉 외에 복지, 직급, 성과급 등 대안도 언급할 수 있어요
- 자연스러운 대화체로 2-4문장으로 답변하세요
- 한국어로 답변하세요"""

    try:
        if data.ai_model == "claude":
            response = await call_claude_chat(system, data.history, data.message)
        elif data.ai_model == "gpt":
            response = await call_gpt_chat(system, data.history, data.message)
        else:
            response = await call_gemini_chat(system, data.history, data.message)
        return {"response": response, "error": None}
    except Exception as e:
        return {"response": None, "error": str(e)}


@app.post("/api/debate")
async def debate(req: DebateRequest):
    p = req.profile
    rounds = max(1, min(req.rounds, 3))
    context = f"직군: {p.role}, 경력: {p.experience}년, 현재연봉: {p.current_salary:,}만원, 희망연봉: {p.desired_salary:,}만원"

    debate_log = []
    history_claude = []
    history_gpt = []
    history_gemini = []

    topic_intro = f"""연봉협상 주제: {req.topic}
상황: {context}

이 연봉협상 상황에 대해 짧고 명확한 의견을 2-3문장으로 제시하세요. 다른 AI의 의견에 동의하거나 반박할 수 있습니다."""

    ai_names = ["claude", "gpt", "gemini"]
    callers = {
        "claude": lambda prompt, hist: call_claude_chat(
            f"당신은 연봉협상 전문가 Claude입니다. {context}", hist, prompt),
        "gpt": lambda prompt, hist: call_gpt_chat(
            f"당신은 연봉협상 전문가 GPT입니다. {context}", hist, prompt),
        "gemini": lambda prompt, hist: call_gemini_chat(
            f"당신은 연봉협상 전문가 Gemini입니다. {context}", hist, prompt),
    }
    histories = {"claude": history_claude, "gpt": history_gpt, "gemini": history_gemini}

    for round_num in range(rounds):
        for ai in ai_names:
            if round_num == 0:
                prompt = topic_intro
            else:
                prev = debate_log[-1]
                prompt = f"""이전 발언:\n{prev['ai'].upper()}: {prev['message']}\n\n이 의견에 대해 동의 또는 반박하며 자신의 관점을 2-3문장으로 이어가세요."""

            hist = histories[ai]
            try:
                response = await callers[ai](prompt, hist)
            except Exception as e:
                response = f"[오류: {str(e)}]"

            hist.append({"role": "user", "content": prompt})
            hist.append({"role": "assistant", "content": response})
            debate_log.append({"round": round_num + 1, "ai": ai, "message": response})

    summary_prompt = f"""다음은 연봉협상 주제 '{req.topic}'에 대한 3개 AI의 토론입니다 ({context}).

토론 내용:
""" + "\n".join(
        f"[라운드{e['round']} - {e['ai'].upper()}]: {e['message']}"
        for e in debate_log
    ) + """

토론을 3-4문장으로 요약하고, 가장 실용적인 합의점을 제시하세요."""

    summary = ""
    for fn in [call_claude, call_gpt, call_gemini]:
        try:
            summary = await fn(summary_prompt)
            break
        except Exception:
            continue

    return {"topic": req.topic, "rounds": rounds, "debate": debate_log, "summary": summary}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
