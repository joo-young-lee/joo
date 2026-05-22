#!/usr/bin/env python3
"""
이주영 취업 트래커 서버
노션 이직 관리 DB + 공고 탐색/핏 판단 연동
실행: python3 job_tracker_server.py
"""

import os, sys, json, subprocess, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

# ─── Notion 설정 ───────────────────────────────────────────────
NOTION_DB_ID        = "312efe628ea180bda553daf1d28a328c"   # 이직 관리 DB
NOTION_DS_ID        = "312efe628ea1802797c1000b9831b8c6"   # 이직 준비 현황 데이터소스

# ─── 환경변수에서 토큰 로드 ────────────────────────────────────
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
CONFIG_PATH = Path(__file__).parent / "tracker_config.json"
DEFAULT_TRACKER_KEYWORDS = [
    "AI 기획", "TPM", "AI 전략기획", "LLM 기획", "생성형 AI 기획",
    "디지털전환 기획", "Technical Program Manager", "플랫폼전략기획",
]
DEFAULT_SCHEDULE_LABEL = "매일 오전 9시"
DEFAULT_TRACKER_SOURCES = [
    "네이버", "카카오", "원티드", "점핏", "리멤버", "사람인",
    "잡플래닛", "프로그래머스", "로켓펀치", "인크루트",
]


def load_tracker_config():
    config = {
        "keywords": DEFAULT_TRACKER_KEYWORDS[:],
        "schedule_label": DEFAULT_SCHEDULE_LABEL,
        "sources": DEFAULT_TRACKER_SOURCES[:],
    }
    if not CONFIG_PATH.exists():
        return config
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return config

    if isinstance(stored.get("keywords"), list) and stored["keywords"]:
        config["keywords"] = [str(k).strip() for k in stored["keywords"] if str(k).strip()]
    if stored.get("schedule_label"):
        config["schedule_label"] = str(stored["schedule_label"]).strip()
    if isinstance(stored.get("sources"), list) and stored["sources"]:
        config["sources"] = [str(s).strip() for s in stored["sources"] if str(s).strip()]
    return config


def save_tracker_config(payload):
    keywords = payload.get("keywords", [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    elif isinstance(keywords, list):
        keywords = [str(k).strip() for k in keywords if str(k).strip()]
    else:
        keywords = []

    config = load_tracker_config()
    if keywords:
        config["keywords"] = keywords
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config

# ─── Notion API 헬퍼 ──────────────────────────────────────────
def notion_request(method, path, body=None):
    if not NOTION_TOKEN:
        return {"error": "NOTION_TOKEN not set"}
    import urllib.request
    url = f"https://api.notion.com/v1{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {NOTION_TOKEN}")
    req.add_header("Notion-Version", "2022-06-28")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  ❌ Notion API {e.code}: {body}")
        return {"error": f"HTTP {e.code}", "detail": body}
    except Exception as e:
        return {"error": str(e)}

def get_jobs():
    """노션 search API로 이직 준비 현황 페이지 가져오기
    (다중 데이터소스 DB는 직접 쿼리 불가 → search 우회)"""
    res = notion_request("POST", "/search", {
        "filter": {"value": "page", "property": "object"},
        "page_size": 100,
        "sort": {"direction": "descending", "timestamp": "last_edited_time"},
    })
    if "error" in res:
        return {"error": res.get("detail", res["error"])}

    def txt(props, p):
        v = props.get(p, {})
        t = v.get("type", "")
        if t == "title":        return "".join(r.get("plain_text","") for r in v.get("title",[]))
        if t == "rich_text":    return "".join(r.get("plain_text","") for r in v.get("rich_text",[]))
        if t == "select":       return (v.get("select") or {}).get("name","")
        if t == "multi_select": return [o.get("name","") for o in v.get("multi_select",[])]
        if t == "url":          return v.get("url","")
        if t == "date":         return (v.get("date") or {}).get("start","")
        return ""

    jobs = []
    all_parent_ids = set()
    for page in res.get("results", []):
        # 부모가 이직 관리 DB 안 페이지만 필터
        parent = page.get("parent", {})
        parent_db = parent.get("database_id", "").replace("-","")
        if parent_db:
            all_parent_ids.add(parent_db)
        if parent_db not in (NOTION_DB_ID.replace("-",""), NOTION_DS_ID.replace("-","")):
            continue
        props = page.get("properties", {})
        # 회사명(title)이 있는 페이지만 포함
        company = txt(props, "회사명")
        if not company:
            continue
        jobs.append({
            "id": page["id"],
            "url": page.get("url",""),
            "company": company,
            "position": txt(props, "포지션"),
            "priority": txt(props, "우선순위"),
            "status": txt(props, "지원 상태"),
            "apply_date": txt(props, "지원일"),
            "interview1": txt(props, "1차 면접일"),
            "interview2": txt(props, "2차 면접일"),
            "interview3": txt(props, "3차 면접일"),
            "final_interview": txt(props, "최종 면접일"),
            "job_url": txt(props, "채용 공고 URL"),
            "memo": txt(props, "메모"),
        })
    # 디버그: 검색에서 발견된 부모 DB ID 목록 출력
    if not jobs:
        print(f"  ℹ️  /search 결과: {len(res.get('results',[]))}개 페이지 발견")
        print(f"  ℹ️  발견된 parent DB IDs: {all_parent_ids}")
        print(f"  ℹ️  찾고 있는 IDs: {NOTION_DB_ID.replace('-','')} / {NOTION_DS_ID.replace('-','')}")
    else:
        print(f"  ✅ 노션 DB에서 {len(jobs)}개 공고 로드됨")
    # 우선순위 정렬
    priority_order = {"높음": 0, "중간": 1, "낮음": 2, "": 3}
    jobs.sort(key=lambda j: priority_order.get(j.get("priority",""), 3))
    return {"jobs": jobs}

def update_job(page_id, updates):
    """노션 이직 관리 DB 페이지 업데이트
    확인된 실제 속성명/타입 기반으로 정확히 매핑
    - 지원 상태: multi_select
    - 우선순위: select (높음/중간/낮음)
    - 지원일/면접일: date
    - 메모/JD: rich_text (API에서 text 컬럼도 rich_text로 업데이트)
    """
    props = {}
    field_map = {
        "status":          ("지원 상태",  "multi_select"),
        "priority":        ("우선순위",   "select"),
        "apply_date":      ("지원일",     "date"),
        "interview1":      ("1차 면접일", "date"),
        "interview2":      ("2차 면접일", "date"),
        "interview3":      ("3차 면접일", "date"),
        "final_interview": ("최종 면접일","date"),
        "memo":            ("메모",       "rich_text"),
        "jd":              ("JD",         "rich_text"),
        "job_url":         ("채용 공고 URL", "url"),
    }
    for key, value in updates.items():
        if key not in field_map:
            continue
        notion_key, ptype = field_map[key]
        if ptype == "select":
            props[notion_key] = {"select": {"name": value}} if value else {"select": None}
        elif ptype == "multi_select":
            vals = value if isinstance(value, list) else [value]
            props[notion_key] = {"multi_select": [{"name": v} for v in vals if v]}
        elif ptype == "date":
            props[notion_key] = {"date": {"start": value}} if value else {"date": None}
        elif ptype == "rich_text":
            props[notion_key] = {"rich_text": [{"type": "text", "text": {"content": str(value)[:2000]}}]}
        elif ptype == "url":
            props[notion_key] = {"url": value if value else None}
    if not props:
        return {"error": "No valid fields to update"}
    return notion_request("PATCH", f"/pages/{page_id}", {"properties": props})

def check_token():
    if not NOTION_TOKEN:
        return {"valid": False, "message": "NOTION_TOKEN이 설정되지 않았습니다."}
    res = notion_request("GET", "/users/me")
    if "error" in res:
        return {"valid": False, "message": res["error"]}
    return {"valid": True, "user": res.get("name",""), "message": "연결 성공!"}

def save_token(token):
    env_path.write_text(f"NOTION_TOKEN={token}\n")
    global NOTION_TOKEN
    NOTION_TOKEN = token
    return {"ok": True}

# ─── 이주영 프로필 키워드 ─────────────────────────────────────
PROFILE_KEYWORDS = [
    "AI 기획", "AI기획", "AI 전략", "TPM", "Technical Program Manager",
    "LLM", "생성형 AI", "GenAI", "AI 에이전트", "디지털 전환", "DX",
    "AI 솔루션", "머신러닝 기획", "데이터 전략", "플랫폼 전략기획",
    "AI 거버넌스", "AI 서비스 기획", "IT기획", "IT 전략"
]

# 핵심 역량 키워드 (핏 점수 계산용)
STRONG_MATCH = [
    "ai", "llm", "gpt", "생성형", "머신러닝", "machine learning",
    "tpm", "technical program", "컨설팅", "consulting", "전략기획",
    "poc", "파일럿", "로드맵", "roadmap", "stakeholder",
    "금융", "보험", "핀테크", "데이터", "python", "sql",
    "디지털 전환", "dx", "거버넌스", "이해관계자"
]
WEAK_MATCH = [
    "flutter", "react native", "ios 개발", "android 개발",
    "java 개발", "c++", "devops", "kubernetes", "embedded",
    "게임 개발", "하드웨어"
]

def quick_fit_score(text):
    """JD 텍스트로 빠른 핏 점수 계산"""
    t = text.lower()
    hits = sum(1 for k in STRONG_MATCH if k in t)
    warns = sum(1 for k in WEAK_MATCH if k in t)
    score = max(20, min(95, int((hits / len(STRONG_MATCH)) * 100 + 15 - warns * 8)))
    verdict = "green" if score >= 70 else "orange" if score >= 45 else "red"
    hit_words = [k for k in STRONG_MATCH if k in t]
    warn_words = [k for k in WEAK_MATCH if k in t]
    return {"score": score, "verdict": verdict, "hits": hit_words, "warns": warn_words}

# ─── 잡보드 자동 탐색 ─────────────────────────────────────────
def search_jobs(keyword, site):
    """잡보드에서 키워드로 채용공고를 검색해 URL+제목 목록 반환"""
    import urllib.request, urllib.parse, html as html_module

    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    def fetch(url, referer=None):
        req = urllib.request.Request(url)
        req.add_header("User-Agent", UA)
        req.add_header("Accept-Language", "ko-KR,ko;q=0.9")
        if referer:
            req.add_header("Referer", referer)
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            enc = r.headers.get_content_charset() or "utf-8"
            try:
                return raw.decode(enc, errors="replace")
            except Exception:
                return raw.decode("utf-8", errors="replace")

    def clean(s):
        s = re.sub(r'<[^>]+>', '', s)
        s = html_module.unescape(s)
        return re.sub(r'\s+', ' ', s).strip()

    results = []

    try:
        if site == "jobkorea":
            enc_kw = urllib.parse.quote(keyword)
            url = f"https://www.jobkorea.co.kr/Search/?stext={enc_kw}&tabType=recruit&Page_No=1"
            html_text = fetch(url, "https://www.jobkorea.co.kr/")
            # 공고 링크 파싱
            for m in re.finditer(r'href="(/Recruit/GI_Read/(\d+)[^"]*)"[^>]*>([^<]{5,80})</a>', html_text):
                title = clean(m.group(3))
                if len(title) > 5 and any(c.isalpha() for c in title):
                    results.append({
                        "title": title,
                        "url": "https://www.jobkorea.co.kr" + m.group(1),
                        "site": "사이버",
                        "id": m.group(2),
                    })

        elif site == "wanted":
            enc_kw = urllib.parse.quote(keyword)
            url = f"https://www.wanted.co.kr/search?query={enc_kw}&tab=position"
            html_text = fetch(url, "https://www.wanted.co.kr/")
            for m in re.finditer(r'href="(/wd/(\d+))"[^>]*>.*?<strong[^>]*>([^<]{5,80})</strong>', html_text, re.DOTALL):
                title = clean(m.group(3))
                if title:
                    results.append({
                        "title": title,
                        "url": "https://www.wanted.co.kr" + m.group(1),
                        "site": "원티드",
                        "id": m.group(2),
                    })
            # 대안 패턴
            if not results:
                for m in re.finditer(r'href="(/wd/\d+)"', html_text):
                    link = "https://www.wanted.co.kr" + m.group(1)
                    if link not in [r["url"] for r in results]:
                        results.append({"title": f"원티드 공고 ({m.group(1)})", "url": link, "site": "원티드", "id": m.group(1)})

        elif site == "saramin":
            enc_kw = urllib.parse.quote(keyword)
            url = f"https://www.saramin.co.kr/zf_user/search/recruit?searchType=search&searchword={enc_kw}&recruitPage=1"
            html_text = fetch(url, "https://www.saramin.co.kr/")
            for m in re.finditer(r'href="(https://www\.saramin\.co\.kr/zf_user/jobs/relay/view\?[^"]+)"[^>]*>\s*<span[^>]*>([^<]{5,80})</span>', html_text, re.DOTALL):
                title = clean(m.group(2))
                if title:
                    results.append({"title": title, "url": m.group(1), "site": "사람인", "id": ""})
            if not results:
                for m in re.finditer(r'data-job-title="([^"]{5,80})"[^>]*data-job-url="([^"]+)"', html_text):
                    results.append({"title": clean(m.group(1)), "url": m.group(2), "site": "사람인", "id": ""})

        elif site == "remember":
            enc_kw = urllib.parse.quote(keyword)
            url = f"https://career.rememberapp.co.kr/jobs?keyword={enc_kw}"
            html_text = fetch(url, "https://career.rememberapp.co.kr/")
            for m in re.finditer(r'href="(/jobs/(\d+))"[^>]*>.*?<[^>]+>([^<]{5,80})</', html_text, re.DOTALL):
                title = clean(m.group(3))
                if title and len(title) > 4:
                    link = "https://career.rememberapp.co.kr" + m.group(1)
                    results.append({"title": title, "url": link, "site": "리멤버", "id": m.group(2)})

    except Exception as e:
        return {"error": str(e), "site": site, "keyword": keyword}

    # 중복 제거
    seen = set()
    unique = []
    for r in results:
        if r["url"] not in seen:
            seen.add(r["url"])
            unique.append(r)

    return {"ok": True, "site": site, "keyword": keyword, "count": len(unique), "jobs": unique[:20]}


def auto_discover(keywords=None):
    """이주영 프로필 기반 주요 잡보드 자동 탐색 + 핏 점수 계산"""
    import concurrent.futures

    search_keywords = [k.strip() for k in (keywords or load_tracker_config()["keywords"]) if k and k.strip()]
    if not search_keywords:
        search_keywords = DEFAULT_TRACKER_KEYWORDS[:]

    search_tasks = []
    for site in ("jobkorea", "wanted", "saramin", "remember"):
        for keyword in search_keywords[:3]:
            search_tasks.append((keyword, site))

    all_results = []
    errors = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(search_jobs, kw, site): (kw, site) for kw, site in search_tasks}
        for future in concurrent.futures.as_completed(futures, timeout=30):
            res = future.result()
            if "error" in res:
                errors.append(res)
            else:
                all_results.extend(res.get("jobs", []))

    # URL 중복 제거
    seen = set()
    unique = []
    for job in all_results:
        if job["url"] not in seen:
            seen.add(job["url"])
            unique.append(job)

    return {
        "ok": True,
        "total": len(unique),
        "jobs": unique[:40],
        "errors": errors,
        "keywords_used": list(dict.fromkeys(kw for kw, _ in search_tasks)),
    }

# ─── JD 크롤러 ────────────────────────────────────────────────
def crawl_jd(url):
    """채용공고 URL에서 JD 텍스트를 추출한다"""
    import urllib.request, html as html_module
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            # 인코딩 감지
            encoding = r.headers.get_content_charset() or "utf-8"
            try:
                html_text = raw.decode(encoding, errors="replace")
            except Exception:
                html_text = raw.decode("utf-8", errors="replace")
    except Exception as e:
        return {"error": f"URL 접근 실패: {str(e)}", "url": url}

    # ─── 사이트별 특화 추출 ───
    domain = urlparse(url).netloc.lower()
    extracted = ""

    # 공통: <script> <style> <nav> <header> <footer> 제거
    def clean_html(h):
        h = re.sub(r'<script[^>]*>.*?</script>', ' ', h, flags=re.DOTALL|re.IGNORECASE)
        h = re.sub(r'<style[^>]*>.*?</style>', ' ', h, flags=re.DOTALL|re.IGNORECASE)
        h = re.sub(r'<nav[^>]*>.*?</nav>', ' ', h, flags=re.DOTALL|re.IGNORECASE)
        h = re.sub(r'<header[^>]*>.*?</header>', ' ', h, flags=re.DOTALL|re.IGNORECASE)
        h = re.sub(r'<footer[^>]*>.*?</footer>', ' ', h, flags=re.DOTALL|re.IGNORECASE)
        h = re.sub(r'<!--.*?-->', ' ', h, flags=re.DOTALL)
        h = re.sub(r'<[^>]+>', ' ', h)
        h = html_module.unescape(h)
        h = re.sub(r'\s+', ' ', h).strip()
        return h

    # 잡코리아
    if "jobkorea" in domain:
        m = re.search(r'class=["\'](?:jobContArea|job-contents|recruit-detail)["\'][^>]*>(.*?)</(?:div|section)', html_text, re.DOTALL|re.IGNORECASE)
        if m: extracted = clean_html(m.group(1))

    # 원티드
    elif "wanted" in domain:
        m = re.search(r'class=["\'](?:JobContent|job-content)["\'][^>]*>(.*?)</(?:section|div)>', html_text, re.DOTALL|re.IGNORECASE)
        if m: extracted = clean_html(m.group(1))

    # 사람인
    elif "saramin" in domain:
        m = re.search(r'class=["\'](?:job_detail_info|recruit_detail)["\'][^>]*>(.*?)</(?:div|section)', html_text, re.DOTALL|re.IGNORECASE)
        if m: extracted = clean_html(m.group(1))

    # 리멤버
    elif "rememberapp" in domain or "remember" in domain:
        m = re.search(r'class=["\'](?:JobDetail|job-detail)["\'][^>]*>(.*?)</(?:div|section)', html_text, re.DOTALL|re.IGNORECASE)
        if m: extracted = clean_html(m.group(1))

    # 채널톡
    elif "channel" in domain:
        m = re.search(r'<main[^>]*>(.*?)</main>', html_text, re.DOTALL|re.IGNORECASE)
        if m: extracted = clean_html(m.group(1))

    # 네이버 / 기타
    if not extracted or len(extracted) < 200:
        # <main> 또는 <article> 우선
        for tag in ['main', 'article', 'section']:
            m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', html_text, re.DOTALL|re.IGNORECASE)
            if m:
                candidate = clean_html(m.group(1))
                if len(candidate) > 300:
                    extracted = candidate
                    break

    # 최후: body 전체 텍스트
    if not extracted or len(extracted) < 200:
        extracted = clean_html(html_text)

    # 너무 길면 5000자 자르기
    extracted = extracted[:5000] if len(extracted) > 5000 else extracted

    # 페이지 타이틀 추출
    title_m = re.search(r'<title[^>]*>(.*?)</title>', html_text, re.IGNORECASE|re.DOTALL)
    title = clean_html(title_m.group(1)) if title_m else url

    return {
        "ok": True,
        "url": url,
        "title": title[:100],
        "content": extracted,
        "length": len(extracted),
        "domain": domain,
    }

# ─── HTTP 서버 ────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 조용한 로그

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            html_path = Path(__file__).parent / "job_tracker.html"
            if html_path.exists():
                self.send_html(html_path.read_text(encoding="utf-8"))
            else:
                self.send_json({"error": "job_tracker.html not found"}, 404)
        elif path == "/api/status":
            self.send_json(check_token())
        elif path == "/api/jobs":
            self.send_json(get_jobs())
        elif path == "/api/daily-jobs":
            daily_path = Path(__file__).parent / "daily_jobs.json"
            if daily_path.exists():
                payload = json.loads(daily_path.read_text(encoding="utf-8"))
                config = load_tracker_config()
                payload.setdefault("schedule_label", config["schedule_label"])
                payload.setdefault("sources", config["sources"])
                payload.setdefault("keywords", config["keywords"])
                self.send_json(payload)
            else:
                self.send_json({"error": "아직 크롤링 결과가 없습니다. daily_crawler.py를 먼저 실행하세요.", "jobs": []})
        elif path == "/api/config":
            self.send_json(load_tracker_config())
        elif path == "/api/discover":
            print("  🔎 잡보드 자동 탐색 시작...")
            self.send_json(auto_discover())
        elif path == "/api/search-jobs":
            keyword = params.get("keyword", ["AI 기획"])[0]
            site = params.get("site", ["jobkorea"])[0]
            print(f"  🔎 탐색: [{site}] {keyword}")
            self.send_json(search_jobs(keyword, site))
        elif path == "/api/fit":
            # URL 파라미터로 빠른 핏 점수만 계산
            text = params.get("text", [""])[0]
            self.send_json(quick_fit_score(text))
        elif path == "/api/debug-notion":
            # 노션 search 결과 원본 출력 (디버그용)
            res = notion_request("POST", "/search", {
                "filter": {"value": "page", "property": "object"},
                "page_size": 20,
                "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            })
            if "error" in res:
                self.send_json({"error": res})
            else:
                pages = res.get("results", [])
                debug_info = []
                for p in pages[:20]:
                    parent = p.get("parent", {})
                    props = p.get("properties", {})
                    title_prop = props.get("회사명", props.get("title", props.get("Name", {})))
                    title = ""
                    if title_prop.get("type") == "title":
                        title = "".join(r.get("plain_text","") for r in title_prop.get("title",[]))
                    elif title_prop.get("type") == "rich_text":
                        title = "".join(r.get("plain_text","") for r in title_prop.get("rich_text",[]))
                    debug_info.append({
                        "id": p["id"],
                        "title": title or "(제목 없음)",
                        "parent_type": parent.get("type",""),
                        "parent_db_id": parent.get("database_id","").replace("-",""),
                        "property_keys": list(props.keys())[:8],
                    })
                self.send_json({
                    "total_results": len(pages),
                    "target_db_id": NOTION_DB_ID.replace("-",""),
                    "target_ds_id": NOTION_DS_ID.replace("-",""),
                    "pages": debug_info,
                })
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/token":
            self.send_json(save_token(body.get("token","")))
        elif path == "/api/config":
            self.send_json(save_tracker_config(body))
        elif path == "/api/run-crawler":
            crawler_path = str(Path(__file__).parent / "daily_crawler.py")
            keywords = body.get("keywords") or load_tracker_config()["keywords"]
            subprocess.Popen(
                [sys.executable, crawler_path, "--keywords-json", json.dumps(keywords, ensure_ascii=False)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.send_json({"ok": True, "message": "크롤러 시작됨. 30~60초 후 결과가 저장됩니다.", "keywords": keywords})
        elif path == "/api/crawl":
            target_url = body.get("url","")
            if not target_url:
                self.send_json({"error": "url 필드가 필요합니다"}, 400)
            else:
                print(f"  🔍 크롤링: {target_url}")
                result = crawl_jd(target_url)
                # 크롤링 성공 시 자동으로 핏 점수 계산
                if result.get("ok") and result.get("content"):
                    result["fit"] = quick_fit_score(result["content"])
                self.send_json(result)
        else:
            self.send_json({"error": "Not found"}, 404)

    def do_PATCH(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/jobs/"):
            page_id = path.split("/api/jobs/")[1]
            self.send_json(update_job(page_id, body))
        else:
            self.send_json({"error": "Not found"}, 404)

if __name__ == "__main__":
    port = 5555
    server = HTTPServer(("localhost", port), Handler)
    print(f"🚀 취업 트래커 서버 시작")
    print(f"   → http://localhost:{port} 열기")
    print(f"   → 종료: Ctrl+C\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n서버 종료")
