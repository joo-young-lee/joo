#!/usr/bin/env python3
"""
HR Career Multi-Agent System
Master Orchestrator + 9 Specialized Sub-Agents
"""

import os, sys, json, re
from datetime import datetime
from pathlib import Path
import anthropic

# ── Config ─────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
PROFILE_FILE = BASE_DIR / ".hr_profile.json"
RESULTS_DIR  = BASE_DIR / "results"
RESULTS_DIR.mkdir(exist_ok=True)

MODEL = "claude-sonnet-4-6"

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

# ── Color helpers ───────────────────────────────────────────────────────────
def c(text, code): return f"\033[{code}m{text}\033[0m"
def bold(t):    return c(t, "1")
def purple(t):  return c(t, "35")
def cyan(t):    return c(t, "36")
def green(t):   return c(t, "32")
def yellow(t):  return c(t, "33")
def dim(t):     return c(t, "2")
def red(t):     return c(t, "31")

def section(title, emoji=""):
    line = "─" * 55
    print(f"\n{purple(line)}")
    print(f"  {emoji}  {bold(title)}")
    print(f"{purple(line)}")

# ── Profile ─────────────────────────────────────────────────────────────────
def load_profile() -> dict:
    if PROFILE_FILE.exists():
        try:
            return json.loads(PROFILE_FILE.read_text())
        except:
            pass
    return {}

def save_profile(data: dict):
    PROFILE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    print(green("✅ 프로필 저장 완료"))

def show_profile():
    p = load_profile()
    if not p:
        print(yellow("⚠️  저장된 프로필 없음. 'profile' 명령어로 설정하세요."))
        return
    section("내 프로필", "👤")
    for k, v in p.items():
        if v:
            label = {"name":"이름","resume":"경력요약","skills":"스킬",
                     "targetRole":"희망포지션","note":"메모"}.get(k, k)
            preview = (v[:80] + "...") if len(str(v)) > 80 else v
            print(f"  {cyan(label):20} {preview}")

def edit_profile():
    p = load_profile()
    fields = [
        ("name",          "이름",           "예: 이주영"),
        ("resume",        "경력요약",       "경력, 스킬, 프로젝트 등 (길게 써도 됩니다)"),
        ("skills",        "핵심 스킬",      "예: Python, SQL, PM, Data Analysis"),
        ("targetRole",    "희망 포지션",    "예: AI 스타트업 Head of Product"),
        ("note",          "기타 메모",      "경력 공백, 자주 쓰는 내용 등"),
    ]
    section("프로필 편집", "👤")
    print(dim("  7��터만 누르면 기존 값 유지됩니다.\n"))
    for key, label, hint in fields:
        cur = p.get(key, "")
        preview = f" {dim(f'[Z切�: {cur[:40]}...]')}" if cur else f" {dim(f'({hint})')}"
        val = input(f"  {cyan(label)}{preview}\n  > ").strip()
        if val:
            p[key] = val
        print()
    save_profile(p)

# ── Sub-Agent Definitions ───────────────────────────────────────────────────
AGENTS = {
    "jd_analysis": {
        "name": "🔍 JD 분석",
        "system": """당신은 이직 리서치 에이전트입니다.
범위는 JD 분석과 지원 판단 보조로 제한됩니다. 이력서/면접/연봉 코칭으로 확장하지 마세요.
사실, 해석, 추정을 구분하고 확인되지 않은 내용은 반드시 '확인 필요'로 표시하세요.

반드시 아래 형식으로 답하세요:
# 한줄 결론
# 회사/직무 핵심 요약
# 경력직 핏 분석
# 지원 우선순위 판단
# 리스크 및 확인 필요 항목
# 다음 액션"""
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

판정은 반드시 `적극 지원 / 조건부 지원 / 보류 또는 제외` 중 하나로 명시하세요."""
    },
    "research": {
        "name": "🏢 종합 리서치",
        "system": """당신은 회사/직무 리서치 에이전트입니다.
최신 여부가 불확실한 외부 사실은 확정적으로 쓰지 말고 '확인 필요'를 붙이세요.
지원 판단에 필요한 정보만 간결하게 정리하세요.

반드시 아래 형식으로 답하세요:
# 한줄 결론
# 회사/직무 핵심 요약
# 경력직 핏 분석
# 지원 우선순위 판단
# 리스크 및 확인 필요 항목
# 다음 액션"""
    },
}

# ── Sub-agent runner ────────────────────────────────────────────────────────
def run_agent(agent_id: str, context: str, task: str) -> str:
    agent = AGENTS[agent_id]
    print(f"\n  {dim('▶')} {agent['name']} {dim('실행 중...')}", end="", flush=True)

    messages = [{
        "role": "user",
        "content": f"【컨텍스트】\n{context}\n\n【요청】\n{task}"
    }]
    response = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system=agent["system"],
        messages=messages,
    )
    result = response.content[0].text
    print(f"\r  {green('✓')} {agent['name']} {dim('완료')}" + " " * 20)
    return result

# ── Master Orchestrator ─────────────────────────────────────────────────────
MASTER_SYSTEM = """당신은 이직 관리/회사 탐색용 총괄 오케스트레이터입니다.

사용 가능한 에이전트 (tool_use로 호출):
- jd_analysis    : JD 분석
- matching       : 나 vs JD 적합도 매칭
- research       : 회사/조직/산업 리서치

규칙:
1. 이직 관리, 회사/직무 찾기, JD/핏 판단 범위의 에이전트만 선택
2. 각 에이전트 결과를 다음 에이전트의 context에 항상 포함
3. 자연스러운 순서로 체이닝 (예: JD받음 → jd_analysis → matching)
4. 에이전트를 모두 실행한 후 최종 한국어 요약 제공
5. tool_use 없이 직접 답하지 말 것 — 반드시 에이전트를 통할 것
6. 이력서 작성, 면접 코칭, 연봉 협상 등 범위 밖 요청은 수행하지 말 것"""

TOOLS = [
    {
        "name": agent_id,
        "description": f"{info['name']} 에이전트를 실행합니다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "context": {
                    "type": "string",
                    "description": "이전 에이전트 결과 + 사용자 프로필 + 관련 정보를 모두 포함"
                },
                "task": {
                    "type": "string",
                    "description": "이 에이전트에게 구체적으로 요청할 내용"
                }
            },
            "required": ["context", "task"]
        }
    }
    for agent_id, info in AGENTS.items()
]

def orchestrate(user_input: str) -> str:
    profile = load_profile()
    profile_ctx = ""
    if profile:
        profile_ctx = "\n\n【사용자 프로필】\n" + "\n".join(
            f"- {k}: {v}" for k, v in profile.items() if v
        )

    messages = [{
        "role": "user",
        "content": f"【사용자 요청】\n{user_input}{profile_ctx}"
    }]

    accumulated_results = {}
    final_summary = ""

    section("마스터 오케스트레이터", "🧠")
    print(dim("  상황 분석 중..."))

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=MASTER_SYSTEM,
            tools=TOOLS,
            messages=messages,
        )

        # Collect text (summary) and tool calls
        tool_calls = []
        text_parts = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append(block)
            elif block.type == "text":
                text_parts.append(block.text)

        if text_parts:
            final_summary = "\n".join(text_parts)

        if not tool_calls or response.stop_reason == "end_turn":
            break

        # Execute each tool call (sub-agent)
        tool_results = []
        for tc in tool_calls:
            agent_id = tc.name
            ctx = tc.input.get("context", "")
            task = tc.input.get("task", "")

            # Inject accumulated results into context
            if accumulated_results:
                prev = "\n\n".join(
                    f"=== {AGENTS[aid]['name']} 결과 ===\n{res}"
                    for aid, res in accumulated_results.items()
                )
                ctx = f"{ctx}\n\n【이전 에이전트 결과】\n{prev}"

            result = run_agent(agent_id, ctx, task)
            accumulated_results[agent_id] = result

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tc.id,
                "content": result,
            })

        # Feed results back to master
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user",      "content": tool_results})

    return accumulated_results, final_summary

# ── Save results ─────────────────────────────────────────────────────────────
def save_results(user_input: str, results: dict, summary: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^\w가-힣]", "_", user_input[:30])
    out  = RESULTS_DIR / f"{ts}_{slug}.md"

    lines = [
        f"# HR 어시스턴트 결과\n",
        f"**요청:** {user_input}  ",
        f"**생성:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
        "---\n",
    ]
    for agent_id, result in results.items():
        lines.append(f"## {AGENTS[agent_id]['name']}\n")
        lines.append(result)
        lines.append("\n---\n")

    if summary:
        lines.append("## 🧠 마스터 종합 요약\n")
        lines.append(summary)

    out.write_text("\n".join(lines), encoding="utf-8")
    return out

# ── Interactive CLI ──────────────────────────────────────────────────────────
BANNER = f"""
{purple('━' * 58)}
  {bold('🎯  이직 리서치 멀티 에이전트')}
  {dim('Job discovery + fit research agents only')}
{purple('━' * 58)}
  {cyan('명령어:')}  {bold('go')} <상황 설명>  │  {bold('profile')}  │  {bold('results')}  │  {bold('quit')}
{purple('━' * 58)}
"""

EXAMPLES = f"""
  {dim('예시:')}
  {yellow('go')} 이직하려는데 내 경력과 맞는지 봐줘. JD 첨부할게요
  {yellow('go')} 카카오 AI팀 시니어 PM 포지션 종합 리서치해줘
"""

def main():
    if not client.api_key:
        print(red("❌ ANTHROPIC_API_KEY가 설정되지 않았습니다."))
        print(dim("   export ANTHROPIC_API_KEY='sk-ant-...'  로 설정 후 실행하세요."))
        sys.exit(1)

    print(BANNER)
    print(EXAMPLES)

    while True:
        try:
            raw = input(f"\n{purple('▶')} ").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{dim('종료합니다.')}")
            break

        if not raw:
            continue

        cmd = raw.lower()

        if cmd in ("quit", "exit", "q", "종료"):
            print(dim("종료합니다."))
            break

        elif cmd == "profile":
            show_profile()

        elif cmd == "edit profile" or cmd == "프로필":
            edit_profile()

        elif cmd == "results":
            files = sorted(RESULTS_DIR.glob("*.md"), reverse=True)[:5]
            if not files:
                print(yellow("저장된 결과가 없습니다."))
            else:
                print(f"\n{bold('최근 결과 파일:')}")
                for f in files:
                    print(f"  {cyan(f.name)}")

        elif cmd.startswith("go ") or (not cmd.startswith(("profile","edit","results","quit","exit","q","종료"))):
            user_input = raw[3:].strip() if raw.lower().startswith("go ") else raw

            try:
                results, summary = orchestrate(user_input)

                # Print results
                for agent_id, result in results.items():
                    section(AGENTS[agent_id]["name"])
                    print(result)

                if summary:
                    section("마스터 종합 요약", "🧠")
                    print(summary)

                # Save
                out_path = save_results(user_input, results, summary)
                print(f"\n{green('✅ 결과 저장:')} {dim(str(out_path.name))}")

            except anthropic.APIError as e:
                print(red(f"❌ API 오류: {e}"))
            except Exception as e:
                print(red(f"❌ 오류: {e}"))
                raise

        else:
            print(dim(f"  알 수 없는 명령어. 'go <상황>' 또는 'profile' 을 입력하세요."))

if __name__ == "__main__":
    main()
