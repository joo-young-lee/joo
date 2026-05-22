#!/usr/bin/env python3
"""
이주영 일일 채용공고 크롤러 v8
회사 직접 크롤링 + Playwright(원티드·점핏·리멤버) + 사람인 OAPI

실행: python3 daily_crawler.py
결과: daily_jobs.json

[설정 방법]
1. 사람인 OAPI key → https://oapi.saramin.co.kr/ 에서 무료 발급 후 아래 입력
2. Playwright → pip3 install playwright && playwright install chromium
"""

import os, sys, re, json, urllib.request, urllib.parse, html as html_module, ssl
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# macOS Python SSL 인증서 문제 우회 (개인용 크롤러에서 안전)
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

OUTPUT_PATH = Path(__file__).parent / "daily_jobs.json"
CONFIG_PATH = Path(__file__).parent / "tracker_config.json"
ENV_PATH = Path(__file__).parent / ".env"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

if ENV_PATH.exists():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

# ─── 사람인 OAPI access-key (https://oapi.saramin.co.kr/ 에서 무료 발급) ────
SARAMIN_API_KEY = os.environ.get("SARAMIN_API_KEY", "")

DEFAULT_TRACKER_KEYWORDS = [
    "AI 기획", "TPM", "AI 전략기획", "LLM 기획", "생성형 AI 기획",
    "디지털전환 기획", "Technical Program Manager", "플랫폼전략기획",
]
DEFAULT_SCHEDULE_LABEL = "매일 오전 9시"
DEFAULT_SOURCES = [
    "네이버", "카카오", "원티드", "점핏", "리멤버", "사람인",
    "잡플래닛", "프로그래머스", "로켓펀치", "인크루트",
]


def _split_keywords(raw):
    if not raw:
        return []
    return [k.strip() for k in raw.split(",") if k.strip()]


def load_tracker_config():
    config = {
        "keywords": DEFAULT_TRACKER_KEYWORDS[:],
        "schedule_label": DEFAULT_SCHEDULE_LABEL,
        "sources": DEFAULT_SOURCES[:],
    }
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(stored.get("keywords"), list) and stored["keywords"]:
                config["keywords"] = [str(k).strip() for k in stored["keywords"] if str(k).strip()]
            if stored.get("schedule_label"):
                config["schedule_label"] = str(stored["schedule_label"]).strip()
            if isinstance(stored.get("sources"), list) and stored["sources"]:
                config["sources"] = [str(s).strip() for s in stored["sources"] if str(s).strip()]
        except Exception:
            pass

    env_keywords = _split_keywords(os.environ.get("TRACKER_KEYWORDS", ""))
    if env_keywords:
        config["keywords"] = env_keywords
    if os.environ.get("TRACKER_SCHEDULE_LABEL"):
        config["schedule_label"] = os.environ["TRACKER_SCHEDULE_LABEL"].strip()
    return config


def build_keyword_sets(keywords):
    cleaned = [k.strip() for k in keywords if k and k.strip()]
    if not cleaned:
        cleaned = DEFAULT_TRACKER_KEYWORDS[:]
    base = cleaned[:8]
    extended = cleaned[:5]
    if not extended:
        extended = base[:5]
    return base, extended

# ─── 핏 점수 ─────────────────────────────────────────────────────
STRONG = ["ai","llm","gpt","생성형","머신러닝","기획","tpm","technical program",
          "전략","컨설팅","poc","roadmap","stakeholder","디지털전환","dx",
          "거버넌스","플랫폼","데이터","서비스기획","it기획","제품","product","pm",
          "ai agent","genai","프로그램매니저","program manager"]
WEAK   = ["프론트엔드","백엔드","frontend","backend","devops","ios","android",
          "flutter","kubernetes","embedded","게임","하드웨어","생산관리",
          "품질관리","회계","세무","영업","마케터"]
MUST   = ["ai","기획","전략","tpm","technical","llm","디지털","플랫폼","product","pm","program"]

def fit_score(title: str, dept="") -> tuple:
    t = (title + " " + dept).lower().replace(" ","")
    hits  = [k for k in STRONG if k.replace(" ","") in t]
    warns = [k for k in WEAK   if k.replace(" ","") in t]
    has_must = any(k.replace(" ","") in t for k in MUST)
    base = 35 + len(hits)*9 - len(warns)*12 - (10 if not has_must else 0)
    score = max(15, min(95, base))
    verdict = "green" if score>=68 else "orange" if score>=45 else "red"
    return score, verdict, hits

def clean(s):
    s = re.sub(r'<[^>]+>','',s); s = html_module.unescape(s)
    return re.sub(r'\s+',' ',s).strip()

def make_job(title, company, url, site, keyword="", dept=""):
    score, verdict, hits = fit_score(title, dept)
    return {"title":title,"company":company,"deadline":"","url":url,"site":site,
            "score":score,"verdict":verdict,"matched":hits,"snippet":"","keyword":keyword}

def fetch_json(url, referer="", headers=None, timeout=12):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Accept","application/json,*/*")
    req.add_header("Accept-Language","ko-KR,ko;q=0.9,en;q=0.8")
    if referer: req.add_header("Referer", referer)
    if headers:
        for k,v in headers.items(): req.add_header(k,v)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as r:
        return json.loads(r.read())

def fetch_html(url, referer="", timeout=12):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Accept","text/html,*/*")
    req.add_header("Accept-Language","ko-KR,ko;q=0.9")
    if referer: req.add_header("Referer", referer)
    with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as r:
        raw = r.read()
        enc = r.headers.get_content_charset() or "utf-8"
        return raw.decode(enc, errors="replace")


# ══════════════════════════════════════════════════════════════════
# 공통 플랫폼 헬퍼
# ══════════════════════════════════════════════════════════════════

def crawl_greenhouse(board_id, company, site_url):
    """Greenhouse ATS 공개 API"""
    jobs = []
    try:
        d = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs?content=true",
                       referer=site_url)
        for job in d.get("jobs",[]):
            title = job.get("title","")
            url   = job.get("absolute_url","") or site_url
            dept  = job.get("departments",[{}])[0].get("name","") if job.get("departments") else ""
            loc   = " ".join([o.get("name","") for o in job.get("offices",[])])
            # 서울/한국 필터 (글로벌 회사)
            if board_id in ["openai","google","microsoft","meta","moloco","sendbird"]:
                if loc and "korea" not in loc.lower() and "seoul" not in loc.lower() and "한국" not in loc:
                    continue
            if title:
                jobs.append(make_job(title, company, url, company, dept=dept))
    except Exception as e:
        print(f"  ⚠️  Greenhouse/{company}: {e}")
    return jobs

def crawl_lever(company_id, company, site_url):
    """Lever ATS 공개 API"""
    jobs = []
    try:
        postings = fetch_json(f"https://api.lever.co/v0/postings/{company_id}?mode=json",
                              referer=site_url)
        for p in (postings if isinstance(postings,list) else []):
            title = p.get("text","")
            url   = p.get("hostedUrl") or p.get("applyUrl","") or site_url
            dept  = p.get("categories",{}).get("department","")
            loc   = p.get("categories",{}).get("location","")
            # 글로벌 회사 서울 필터
            if company_id in ["moloco","sendbird"]:
                if loc and "korea" not in loc.lower() and "seoul" not in loc.lower():
                    continue
            if title:
                jobs.append(make_job(f"{title} ({dept})" if dept else title,
                                     company, url, company, dept=dept))
    except Exception as e:
        print(f"  ⚠️  Lever/{company}: {e}")
    return jobs

def crawl_recruiter_kr(subdomain, company, fallback_url=""):
    """recruiter.co.kr (국내 ATS) JSON API"""
    jobs = []
    base = f"https://{subdomain}.recruiter.co.kr"
    try:
        d = fetch_json(f"{base}/app/jobnotice/list", referer=base+"/")
        items = d if isinstance(d,list) else d.get("data", d.get("list",[]))
        for job in items:
            title = (job.get("noticeName") or job.get("title") or job.get("recruitTitle","")).strip()
            sn    = job.get("jobnoticeSn","")
            url   = f"{base}/app/jobnotice/view?systemKindCode=MRS2&jobnoticeSn={sn}" if sn else fallback_url or base
            if title:
                jobs.append(make_job(title, company, url, company))
    except Exception as e:
        print(f"  ⚠️  Recruiter.kr/{company}: {e}")
    return jobs


# ══════════════════════════════════════════════════════════════════
# 네카라쿠배당토 + 국내 빅테크
# ══════════════════════════════════════════════════════════════════

def crawl_naver():
    jobs = []
    seen = set()
    for kw in ["AI","기획","전략기획","TPM",""]:
        enc = urllib.parse.quote(kw)
        for api_url in [
            f"https://recruit.navercorp.com/api/recruits?searchTxt={enc}&entTypeCd=&workTypeCd=1000&page=1",
            f"https://recruit.navercorp.com/rcrt/list.json?searchTxt={enc}&entTypeCd=&workTypeCd=1000",
            f"https://recruit.navercorp.com/rcrt/jsonList.do?searchTxt={enc}&workTypeCd=1000",
        ]:
            try:
                d = fetch_json(api_url, referer="https://recruit.navercorp.com/",
                               headers={"X-Requested-With":"XMLHttpRequest","Accept":"application/json"})
                items = d if isinstance(d,list) else (
                    d.get("list") or d.get("recruitList") or d.get("data") or
                    d.get("recruits") or d.get("result",{}).get("list") or [])
                if not isinstance(items, list): items = []
                for job in items[:20]:
                    if not isinstance(job, dict): continue
                    title  = job.get("jobNm") or job.get("rcrtTitle") or job.get("title","")
                    job_id = str(job.get("rcrtSeq","") or job.get("id","") or job.get("seq",""))
                    url    = f"https://recruit.navercorp.com/rcrt/view.do?rcrtNo={job_id}" if job_id else "https://recruit.navercorp.com"
                    if title and url not in seen:
                        seen.add(url)
                        jobs.append(make_job(title,"네이버",url,"네이버",kw))
                if jobs: break
            except Exception: continue
        if jobs: break
    if not jobs:
        print(f"  ⚠️  네이버: API 엔드포인트 미확인 (Playwright 필요)")
    return jobs

def crawl_kakao():
    jobs = []
    # 카카오 careers API — 여러 엔드포인트 시도
    for api_url in [
        "https://careers.kakao.com/api/rest/recruit/v2/jobs?co=KAKAO&page=0&size=100",
        "https://careers.kakao.com/api/v1/jobs?keyword=&page=0&size=100&part=&jobType=",
        "https://careers.kakao.com/jobs?kakao_source=careers&part=all&jobs=all",
    ]:
        try:
            d = fetch_json(api_url, referer="https://careers.kakao.com/",
                           headers={"Accept":"application/json","X-Requested-With":"XMLHttpRequest"})
            items = (d.get("jobList") or d.get("data") or
                     d.get("list") or d.get("result",{}).get("jobs") or [])
            if not isinstance(items, list): items = []
            for job in items:
                if not isinstance(job, dict): continue
                title  = job.get("jobOfferTitle") or job.get("title","")
                job_id = job.get("jobOfferId") or job.get("id","")
                url    = f"https://careers.kakao.com/jobs/{job_id}" if job_id else "https://careers.kakao.com"
                if title: jobs.append(make_job(title,"카카오",url,"카카오"))
            if jobs: break
        except Exception:
            continue
    if not jobs:
        # HTML 파싱 fallback
        try:
            html = fetch_html("https://careers.kakao.com/jobs?part=all&jobs=all",
                              referer="https://careers.kakao.com/")
            for m in re.finditer(
                    r'data-job-id="(\d+)"[^>]*>.*?class="[^"]*tit_info[^"]*"[^>]*>([^<]+)',
                    html, re.DOTALL):
                jid, title = m.group(1), clean(m.group(2))
                if title:
                    jobs.append(make_job(title,"카카오",
                        f"https://careers.kakao.com/jobs/{jid}","카카오"))
        except Exception as e:
            print(f"  ⚠️  카카오: {e}")
    return jobs

def crawl_kakaobank():
    return crawl_recruiter_kr("kakaobank","카카오뱅크","https://kakaobank.recruiter.co.kr")

def crawl_coupang():
    return crawl_lever("coupang","쿠팡","https://www.coupang.jobs/kr/")

def crawl_woowa():
    jobs = []
    for api_url in [
        "https://career.woowahan.com/api/v1/recruit/list?page=0&size=40",
        "https://career.woowahan.com/api/recruitings?searchKeyword=&page=0&size=40",
        "https://career.woowahan.com/api/v2/recruitings?page=0&size=40",
        "https://career.woowahan.com/api/v1/recruitment?page=0&size=40",
    ]:
        try:
            d = fetch_json(api_url, referer="https://career.woowahan.com/",
                           headers={"X-Requested-With":"XMLHttpRequest"})
            items = (d.get("data",{}).get("content") if isinstance(d.get("data"), dict) else None) or \
                    d.get("recruitings") or d.get("data") or d.get("list") or d.get("content") or []
            if not isinstance(items, list): items = []
            for job in items:
                if not isinstance(job, dict): continue
                title = job.get("recruitingTitle") or job.get("title","")
                rid   = job.get("recruitingId") or job.get("id","")
                url   = f"https://career.woowahan.com/recruitment/{rid}" if rid else "https://career.woowahan.com"
                if title: jobs.append(make_job(title,"배달의민족",url,"배달의민족"))
            if jobs: break
        except Exception:
            continue
    if not jobs:
        print(f"  ⚠️  배달의민족: API 엔드포인트 미확인")
    return jobs

def crawl_daangn():
    # Karrot = 당근마켓 영문 브랜드 Greenhouse board
    jobs = crawl_greenhouse("karrot","당근마켓","https://about.daangn.com/jobs/")
    if not jobs:
        jobs = crawl_greenhouse("daangn","당근마켓","https://about.daangn.com/jobs/")
    return jobs

def crawl_toss():
    # 토스: Lever → Greenhouse 순서로 시도
    jobs = crawl_lever("vivarepublica","토스","https://toss.im/career/jobs")
    if not jobs:
        jobs = crawl_greenhouse("vivarepublica","토스","https://toss.im/career/jobs")
    if not jobs:
        jobs = crawl_greenhouse("toss","토스","https://toss.im/career/jobs")
    return jobs

def crawl_tossbank():
    jobs = crawl_greenhouse("tossbank","토스뱅크","https://tossbank.com/jobs")
    if not jobs:
        jobs = crawl_lever("tossbank","토스뱅크","https://tossbank.com/jobs")
    return jobs

def crawl_line():
    jobs = []
    try:
        d = fetch_json(
            "https://careers.linecorp.com/api/jobs?ca=&fu=&lo=Seoul&ty=&le=&la=&bu=&sbu=",
            referer="https://careers.linecorp.com/")
        items = d if isinstance(d,list) else d.get("jobs") or d.get("data",[])
        for job in items[:40]:
            title  = job.get("title","") or job.get("jobTitle","")
            job_id = job.get("id","")
            url    = f"https://careers.linecorp.com/jobs/{job_id}" if job_id else "https://careers.linecorp.com"
            if title: jobs.append(make_job(title,"라인",url,"라인"))
    except Exception as e:
        print(f"  ⚠️  라인: {e}")
    return jobs

def crawl_zigbang():
    jobs = crawl_greenhouse("zigbang","직방","https://www.zigbang.com/company/recruit")
    if not jobs:
        jobs = crawl_recruiter_kr("zigbang","직방","https://zigbang.recruiter.co.kr")
    return jobs

def crawl_yanolja():
    return crawl_recruiter_kr("yanolja","야놀자","https://careers.yanolja.co/")

def crawl_dunamu():
    # 두나무(업비트): Lever → Greenhouse → 자체 페이지
    jobs = crawl_lever("dunamu","두나무","https://dunamu.com/careers")
    if not jobs:
        jobs = crawl_greenhouse("dunamu","두나무","https://dunamu.com/careers")
    if not jobs:
        # 두나무 자체 채용 페이지 (SPA)
        try:
            d = fetch_json("https://dunamu.com/ko/career/jobs",
                           referer="https://dunamu.com/",
                           headers={"Accept":"application/json"})
            for job in (d.get("jobs") or d.get("list") or d.get("data") or []):
                if not isinstance(job, dict): continue
                title = job.get("title","") or job.get("jobTitle","")
                jid   = job.get("id","")
                url   = f"https://dunamu.com/ko/career/jobs/{jid}" if jid else "https://dunamu.com/careers"
                if title: jobs.append(make_job(title,"두나무",url,"두나무"))
        except Exception:
            pass
    return jobs

def crawl_moloco():
    # 미국 본사지만 서울 오피스 채용
    jobs = crawl_greenhouse("moloco","몰로코","https://www.moloco.com/ko/careers")
    if not jobs:
        jobs = crawl_lever("moloco","몰로코","https://www.moloco.com/ko/careers")
    return jobs

def crawl_sendbird():
    jobs = crawl_greenhouse("sendbird","센드버드","https://sendbird.com/careers")
    if not jobs:
        jobs = crawl_lever("sendbird","센드버드","https://sendbird.com/careers")
    return jobs

def crawl_musinsa():
    jobs = crawl_greenhouse("musinsa","무신사","https://career.musinsa.com/")
    if not jobs:
        jobs = crawl_lever("musinsa","무신사","https://career.musinsa.com/")
    if not jobs:
        # 무신사 자체 채용 페이지
        try:
            d = fetch_json("https://career.musinsa.com/api/jobs",
                           referer="https://career.musinsa.com/")
            for job in (d.get("jobs") or d.get("list") or d.get("data") or []):
                if not isinstance(job, dict): continue
                title = job.get("title","") or job.get("jobTitle","")
                if title:
                    jobs.append(make_job(title,"무신사",
                        job.get("url","https://career.musinsa.com"),"무신사"))
        except Exception:
            pass
    return jobs

def crawl_channel_talk():
    jobs = crawl_greenhouse("channel-io","채널톡","https://channel.io/ko/careers")
    if not jobs:
        jobs = crawl_greenhouse("channel.io","채널톡","https://channel.io/ko/careers")
    return jobs


# ══════════════════════════════════════════════════════════════════
# 금융 / 핀테크
# ══════════════════════════════════════════════════════════════════

def crawl_hyundai_card():
    return crawl_recruiter_kr("hyundaicard","현대카드","https://hyundaicard.recruiter.co.kr")

def crawl_kb():
    return crawl_recruiter_kr("kbfg","KB금융","https://kbfg.recruiter.co.kr")

def crawl_shinhan():
    jobs = crawl_recruiter_kr("shinhan","신한금융","https://shinhan.recruiter.co.kr")
    # 신한투자증권 별도 API
    try:
        d = fetch_json(
            "https://www.shinhaninvest.com/siw/main/common/recruit/getRecruitList.do",
            headers={"X-Requested-With":"XMLHttpRequest"},
            referer="https://www.shinhaninvest.com/")
        for job in d.get("recruitList") or d.get("list") or []:
            title = job.get("recrtTitle") or job.get("title","")
            if title: jobs.append(make_job(title,"신한투자증권",
                "https://www.shinhaninvest.com/siw/main/common/recruit/view.do","신한투자증권"))
    except Exception:
        pass
    return jobs

def crawl_hana():
    return crawl_recruiter_kr("hanaif","하나금융","https://hanaif.recruiter.co.kr")

def crawl_woori():
    return crawl_recruiter_kr("wooribank","우리금융","https://wooribank.recruiter.co.kr")

def crawl_nh():
    return crawl_recruiter_kr("nhrecruit","농협","https://nhrecruit.recruiter.co.kr")

def crawl_hanwha_life():
    jobs = []
    try:
        d = fetch_json("https://recruit.hanwhalife.com/api/v1/jobs?page=1&size=30",
                       referer="https://recruit.hanwhalife.com/")
        for job in d.get("data") or d.get("list") or []:
            title = job.get("title") or job.get("jobTitle","")
            url   = job.get("url","") or "https://recruit.hanwhalife.com"
            if title: jobs.append(make_job(title,"한화생명",url,"한화생명"))
    except Exception as e:
        print(f"  ⚠️  한화생명: {e}")
    return jobs

def crawl_samsung_life():
    jobs = []
    try:
        d = fetch_json("https://www.samsunglife.com/recruitment/api/list?page=1&rows=30",
                       referer="https://www.samsunglife.com/recruitment/")
        for job in d.get("list") or d.get("data") or []:
            title = job.get("title") or job.get("rcrtTtle","")
            url   = job.get("url","") or "https://www.samsunglife.com/recruitment/"
            if title: jobs.append(make_job(title,"삼성생명",url,"삼성생명"))
    except Exception as e:
        print(f"  ⚠️  삼성생명: {e}")
    return jobs

def crawl_axa():
    return crawl_recruiter_kr("axa","AXA손해보험","https://www.axa.co.kr/recruit/")


# ══════════════════════════════════════════════════════════════════
# 대기업
# ══════════════════════════════════════════════════════════════════

def crawl_hyundai_motor():
    jobs = []
    try:
        d = fetch_json(
            "https://careers.hyundai.com/kor/api/jobs?page=1&size=20&keyword=AI",
            referer="https://careers.hyundai.com/")
        for job in d.get("list") or d.get("jobs") or d.get("data",[]):
            title = job.get("title") or job.get("jobTitle","")
            url   = job.get("url","") or "https://careers.hyundai.com"
            if title: jobs.append(make_job(title,"현대자동차",url,"현대자동차"))
    except Exception as e:
        print(f"  ⚠️  현대자동차: {e}")
    return jobs

def crawl_skt():
    jobs = []
    try:
        d = fetch_json(
            "https://careers.sktelecom.com/api/v1/positions?page=1&pageSize=30&keyword=AI",
            referer="https://careers.sktelecom.com/")
        for job in d.get("content") or d.get("list") or d.get("data",[]):
            title = job.get("title") or job.get("positionNm","")
            url   = job.get("url","") or "https://careers.sktelecom.com"
            if title: jobs.append(make_job(title,"SK텔레콤",url,"SK텔레콤"))
    except Exception as e:
        print(f"  ⚠️  SK텔레콤: {e}")
    return jobs

def crawl_lgcns():
    jobs = []
    try:
        d = fetch_json(
            "https://www.lgcns.com/api/recruit/list?page=1&size=20&keyword=AI",
            referer="https://www.lgcns.com/career/")
        for job in d.get("list") or d.get("data") or []:
            title = job.get("title") or job.get("recrtTitle","")
            url   = "https://www.lgcns.com/career/"
            if title: jobs.append(make_job(title,"LG CNS",url,"LG CNS"))
    except Exception as e:
        print(f"  ⚠️  LG CNS: {e}")
    return jobs

def crawl_oliveyoung():
    jobs = crawl_greenhouse("oliveyoung","올리브영","https://careers.oliveyoung.com/")
    if not jobs:
        jobs = crawl_recruiter_kr("oliveyoung","올리브영","https://oliveyoung.recruiter.co.kr")
    return jobs

def crawl_ssg():
    return crawl_recruiter_kr("ssgrecruit","신세계/SSG","https://ssgrecruit.co.kr/")


# ══════════════════════════════════════════════════════════════════
# 삼성 커리어스 (삼성그룹 통합 + 삼성전자)
# ══════════════════════════════════════════════════════════════════

def crawl_samsung():
    jobs = []
    for kw in ["AI", "기획", "전략", "디지털", ""]:
        enc = urllib.parse.quote(kw)
        for api_url in [
            # 삼성그룹 통합채용 Ajax
            f"https://www.samsungcareers.com/rec/retrieveExternalRecruitList.do"
            f"?searchKeyword={enc}&pageIndex=1&pageSize=30",
            # 삼성전자 career.samsung.com
            f"https://career.samsung.com/api/v1/recruit/list"
            f"?page=1&pageSize=30&recruitType=CAREER&keyword={enc}",
            # 삼성 채용 JSON 검색
            f"https://www.samsungcareers.com/api/v1/jobs?keyword={enc}&page=1",
        ]:
            try:
                d = fetch_json(api_url, referer="https://www.samsungcareers.com/",
                               headers={"X-Requested-With": "XMLHttpRequest"})
                items = (d if isinstance(d, list) else
                         d.get("list") or d.get("data") or d.get("jobs") or
                         d.get("recruitList") or d.get("result",{}).get("list") or [])
                for job in items[:30]:
                    title = (job.get("title") or job.get("jobTitle") or
                             job.get("recrtTitle") or job.get("recruitTitle",""))
                    rid   = job.get("id","") or job.get("recruitSeq","") or job.get("jobId","")
                    url   = (job.get("url","") or
                             f"https://www.samsungcareers.com/rec/viewExternalRecruit.do?recIdx={rid}"
                             if rid else "https://www.samsungcareers.com")
                    if title: jobs.append(make_job(title, "삼성", url, "삼성커리어스", kw))
                if jobs: break
            except Exception: continue
        if jobs: break
    if not jobs:
        print(f"  ⚠️  삼성커리어스: API 미확인 (Playwright 필요)")
    return jobs


# ══════════════════════════════════════════════════════════════════
# SK 커리어스 (SK그룹 통합)
# ══════════════════════════════════════════════════════════════════

def crawl_sk_careers():
    jobs = []
    for kw in ["AI", "기획", "전략", "디지털", ""]:
        enc = urllib.parse.quote(kw)
        for api_url in [
            f"https://www.skcareers.com/api/v1/jobs?keyword={enc}&page=1&size=30",
            f"https://www.skcareers.com/recruit/getJobOpeningList.do?keyword={enc}&pageIndex=1",
            f"https://careers.sk.com/api/jobs?keyword={enc}&page=1",
        ]:
            try:
                d = fetch_json(api_url, referer="https://www.skcareers.com/",
                               headers={"X-Requested-With": "XMLHttpRequest"})
                items = (d if isinstance(d, list) else
                         d.get("list") or d.get("data") or d.get("jobs") or
                         d.get("jobOpeningList") or d.get("result",{}).get("list") or [])
                for job in items[:30]:
                    title = (job.get("title") or job.get("jobTitle") or
                             job.get("jobOpeningTitle",""))
                    jid   = job.get("id","") or job.get("jobOpeningId","")
                    url   = (job.get("url","") or
                             f"https://www.skcareers.com/jobs/{jid}" if jid
                             else "https://www.skcareers.com")
                    if title: jobs.append(make_job(title, "SK그룹", url, "SK커리어스", kw))
                if jobs: break
            except Exception: continue
        if jobs: break
    if not jobs:
        print(f"  ⚠️  SK커리어스: API 미확인 (Playwright 필요)")
    return jobs


# ══════════════════════════════════════════════════════════════════
# CJ그룹 인재채용
# ══════════════════════════════════════════════════════════════════

def crawl_cj():
    jobs = []
    for kw in ["AI", "기획", "전략", "디지털", ""]:
        enc = urllib.parse.quote(kw)
        for api_url in [
            f"https://careers.cj.net/api/v1/jobs?keyword={enc}&page=1&size=30",
            f"https://careers.cj.net/api/jobs?keyword={enc}&page=1",
            # recruiter.co.kr CJ 시도
        ]:
            try:
                d = fetch_json(api_url, referer="https://careers.cj.net/",
                               headers={"X-Requested-With": "XMLHttpRequest"})
                items = (d if isinstance(d, list) else
                         d.get("list") or d.get("data") or d.get("jobs") or
                         d.get("result",{}).get("list") or [])
                for job in items[:30]:
                    title = job.get("title") or job.get("jobTitle","")
                    jid   = job.get("id","")
                    url   = (job.get("url","") or
                             f"https://careers.cj.net/jobs/{jid}" if jid
                             else "https://careers.cj.net")
                    if title: jobs.append(make_job(title, "CJ그룹", url, "CJ커리어스", kw))
                if jobs: break
            except Exception: continue
        if jobs: break
    # recruiter.co.kr CJ 계열사 시도
    if not jobs:
        for subdomain, name in [("cj", "CJ그룹"), ("cjenm", "CJ ENM"), ("cjlogistics", "CJ대한통운")]:
            jobs.extend(crawl_recruiter_kr(subdomain, name, f"https://{subdomain}.recruiter.co.kr"))
    if not jobs:
        print(f"  ⚠️  CJ커리어스: API 미확인 (Playwright 필요)")
    return jobs


# ══════════════════════════════════════════════════════════════════
# 글로벌 빅테크 (서울 오피스)
# ══════════════════════════════════════════════════════════════════

def crawl_google():
    jobs = []
    for api_url in [
        # 구글 커리어즈 공개 API (여러 형식)
        "https://careers.google.com/api/jobs/list?location.region=SOUTH+KOREA&page_size=20",
        "https://careers.google.com/api/jobs/list/?company_name=Google&location=Seoul,+South+Korea&page_size=20",
        "https://careers.google.com/api/jobs/list?jlo=ko_KR&page_size=20",
    ]:
        try:
            d = fetch_json(api_url, referer="https://careers.google.com/",
                           headers={"Accept":"application/json"})
            job_list = d.get("jobs") or d.get("result") or d.get("data") or []
            if not isinstance(job_list, list): job_list = []
            for job in job_list:
                if not isinstance(job, dict): continue
                title  = job.get("title","")
                job_id = job.get("job_id","") or job.get("id","")
                url    = f"https://careers.google.com/jobs/results/{job_id}" if job_id else "https://careers.google.com"
                if title: jobs.append(make_job(title,"Google",url,"Google"))
            if jobs: break
        except Exception: continue
    if not jobs:
        print(f"  ⚠️  Google: API 미확인 (Playwright 필요)")
    return jobs

def crawl_microsoft():
    jobs = []
    for api_url in [
        # Korea/Seoul 필터
        "https://gcsservices.careers.microsoft.com/search/api/v1/search?lc=Seoul%2C+South+Korea&pgSz=30",
        "https://gcsservices.careers.microsoft.com/search/api/v1/search?lc=Korea&pgSz=30",
        # query 없이 전체
        "https://gcsservices.careers.microsoft.com/search/api/v1/search?lc=Seoul&pgSz=20&lang=ko_kr",
    ]:
        try:
            d = fetch_json(api_url, referer="https://careers.microsoft.com/",
                           headers={"Accept":"application/json"})
            job_list = (d.get("operationResult",{}).get("result",{}).get("jobs") or
                        d.get("jobs") or d.get("value") or [])
            if not isinstance(job_list, list): job_list = []
            for job in job_list:
                if not isinstance(job, dict): continue
                title  = job.get("title","") or job.get("jobTitle","")
                job_id = job.get("jobId","") or job.get("id","")
                url    = f"https://careers.microsoft.com/us/en/job/{job_id}" if job_id else "https://careers.microsoft.com"
                if title: jobs.append(make_job(title,"Microsoft",url,"Microsoft"))
            if jobs: break
        except Exception: continue
    if not jobs:
        print(f"  ⚠️  Microsoft: API 미확인")
    return jobs

def crawl_meta():
    jobs = []
    for api_url in [
        # Meta careers GraphQL-like 검색
        "https://www.metacareers.com/ajax/jobs/?teams[0]=0&page=1&results_per_page=20&qualifications[0]=0&offices[0]=Seoul",
        "https://www.metacareers.com/jobs?offices[]=Seoul",
        # 한국 오피스 검색 (다른 파라미터)
        "https://www.metacareers.com/ajax/jobs/?location=Seoul%2C+South+Korea&page=1",
    ]:
        try:
            d = fetch_json(api_url, referer="https://www.metacareers.com/",
                           headers={"X-Requested-With":"XMLHttpRequest","Accept":"application/json"})
            job_list = (d.get("data") or d.get("jobs") or d.get("job_data") or [])
            if not isinstance(job_list, list): job_list = []
            for job in job_list:
                if not isinstance(job, dict): continue
                title = job.get("title","") or job.get("job_title","")
                jid   = job.get("id","")
                url   = f"https://www.metacareers.com/jobs/{jid}" if jid else "https://www.metacareers.com/jobs"
                if title: jobs.append(make_job(title,"Meta",url,"Meta"))
            if jobs: break
        except Exception: continue
    if not jobs:
        print(f"  ⚠️  Meta: API 미확인 (Playwright 필요)")
    return jobs

def crawl_openai():
    # OpenAI는 전 세계 Remote 가능 포지션 포함
    return crawl_greenhouse("openai","OpenAI","https://openai.com/careers")


# ══════════════════════════════════════════════════════════════════
# 원티드 — job_group_id 직군 기반 (키워드 쿼리 → 422 오류)
# ══════════════════════════════════════════════════════════════════
# 원티드 직군 ID (기획/PM 계열)
# 518=IT기획·PM  512=기획·전략  514=서비스기획  507=전략기획
WANTED_GROUP_IDS = [518, 512, 514, 507]

def crawl_wanted():
    jobs = []
    seen = set()
    for gid in WANTED_GROUP_IDS:
        try:
            d = fetch_json(
                f"https://www.wanted.co.kr/api/v4/jobs"
                f"?tag_type_ids=&job_group_id={gid}&years=-1&locations=all"
                f"&job_sort=job.latest_order&limit=100&offset=0",
                referer="https://www.wanted.co.kr/",
                headers={"Wanted-User-Agent": "wanted_oss",
                         "Accept": "application/json, text/plain, */*"})
            for job in d.get("data", []):
                jid = str(job.get("id",""))
                if jid in seen: continue
                seen.add(jid)
                title   = job.get("position","")
                co      = job.get("company",{})
                company = job.get("company_name","") or (co.get("name","") if isinstance(co,dict) else "")
                url     = f"https://www.wanted.co.kr/wd/{jid}" if jid else "https://www.wanted.co.kr"
                if title:
                    jobs.append(make_job(title, company, url, "원티드"))
        except Exception as e:
            print(f"  ⚠️  원티드[gid={gid}]: {e}")
    return jobs


# ══════════════════════════════════════════════════════════════════
# 점핏 — 전체 최신순 페이지네이션 + fit_score 필터
# ══════════════════════════════════════════════════════════════════

def crawl_jumpit():
    jobs = []
    seen = set()
    for page in range(1, 5):  # 최신 4페이지
        try:
            d = fetch_json(
                f"https://jumpit.saramin.co.kr/api/positions?sort=rdt&page={page}",
                referer="https://www.jumpit.co.kr/",
                headers={"Accept": "application/json"})
            result   = d.get("result", {})
            positions = result.get("positions", [])
            if not positions:
                break
            for pos in positions:
                pid     = str(pos.get("id",""))
                if pid in seen: continue
                seen.add(pid)
                title   = pos.get("title","")
                company = pos.get("companyName","")
                url     = f"https://www.jumpit.co.kr/position/{pid}" if pid else "https://www.jumpit.co.kr"
                if title:
                    jobs.append(make_job(title, company, url, "점핏"))
        except Exception as e:
            print(f"  ⚠️  점핏[page={page}]: {e}")
            break
    return jobs


# ══════════════════════════════════════════════════════════════════
# 로켓펀치 — 스타트업/테크 특화
# ══════════════════════════════════════════════════════════════════

def crawl_rocketpunch(keyword):
    jobs = []
    try:
        enc = urllib.parse.quote(keyword)
        for api_url in [
            f"https://rocketpunch.com/api/v1/jobs?keyword={enc}&page=1",
            f"https://rocketpunch.com/api/jobs?keywords={enc}&page=1",
        ]:
            try:
                d = fetch_json(api_url, referer="https://rocketpunch.com/jobs",
                               headers={"Accept":"application/json","X-Requested-With":"XMLHttpRequest"})
                objects = (d.get("results",{}).get("objects") or d.get("jobs") or
                           d.get("data") or d.get("result",{}).get("jobs") or [])
                for job in objects:
                    title   = job.get("title","") or job.get("name","")
                    co      = job.get("company",{})
                    company = co.get("name","") if isinstance(co,dict) else str(co)
                    slug    = job.get("slug","") or str(job.get("id",""))
                    url     = f"https://rocketpunch.com/job/{slug}" if slug else "https://rocketpunch.com/jobs"
                    if title:
                        jobs.append(make_job(title, company, url, "로켓펀치", keyword))
                if jobs: break
            except Exception:
                continue
    except Exception as e:
        print(f"  ⚠️  로켓펀치[{keyword}]: {e}")
    return jobs


# ══════════════════════════════════════════════════════════════════
# 사람인 RSS (집계 보조)
# ══════════════════════════════════════════════════════════════════

def crawl_saramin_rss(keyword):
    jobs = []
    try:
        enc  = urllib.parse.quote(keyword)
        # 사람인 RSS - 여러 URL 시도
        for rss_url in [
            f"https://www.saramin.co.kr/zf_user/rss/rss.xml?searchword={enc}&searchType=search",
            f"https://www.saramin.co.kr/zf_user/rss/rss?searchword={enc}&searchType=search",
        ]:
            try:
                html = fetch_html(rss_url, "https://www.saramin.co.kr/")
                if "<item>" not in html:
                    continue
                for item_text in re.findall(r'<item>(.*?)</item>', html, re.DOTALL)[:20]:
                    def g(tag):
                        m = re.search(rf'<{tag}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{tag}>', item_text, re.DOTALL)
                        return m.group(1).strip() if m else ""
                    raw = g("title"); link = g("link").strip()
                    if ' - ' in raw:
                        company, title = raw.split(' - ',1)
                    else:
                        company, title = g("companyName"), raw
                    title = clean(title); company = clean(company)
                    if title and len(title)>3:
                        jobs.append(make_job(title, company, link, "사람인", keyword))
                if jobs:
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"  ⚠️  사람인RSS [{keyword}]: {e}")
    return jobs


# ══════════════════════════════════════════════════════════════════
# 사람인 (Saramin) JSON 검색 — RSS 보완
# ══════════════════════════════════════════════════════════════════

def crawl_saramin(keyword):
    """사람인 검색 결과 HTML → 공고 제목/회사/링크 파싱"""
    jobs = []
    try:
        enc = urllib.parse.quote(keyword)
        # 사람인 검색 결과 페이지 fetch (Ajax JSON 엔드포인트)
        for url in [
            f"https://www.saramin.co.kr/zf_user/jobs/list/domestic-recruit-list-ajax"
            f"?searchword={enc}&searchType=search&recruitPage=1&recruitPageCount=40&recruitSort=relation",
            f"https://www.saramin.co.kr/zf_user/search/recruit-ajax-result"
            f"?searchType=search&searchword={enc}&recruitPage=1&recruitPageCount=40",
        ]:
            try:
                html = fetch_html(url, "https://www.saramin.co.kr/")
                # 공고 블록 파싱
                blocks = re.findall(
                    r'class="[^"]*job_tit[^"]*".*?<a[^>]+href="([^"]+)"[^>]*title="([^"]+)"',
                    html, re.DOTALL)
                for href, title in blocks[:20]:
                    href = href.strip()
                    if not href.startswith("http"):
                        href = "https://www.saramin.co.kr" + href
                    title = clean(title)
                    if title and len(title) > 3:
                        jobs.append(make_job(title, "", href, "사람인", keyword))
                # 회사명 보완
                co_blocks = re.findall(r'class="[^"]*corp_name[^"]*".*?<a[^>]*>([^<]+)</a>', html, re.DOTALL)
                for i, co in enumerate(co_blocks):
                    if i < len(jobs):
                        jobs[i]["company"] = clean(co)
                if jobs:
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"  ⚠️  사람인[{keyword}]: {e}")
    return jobs


# ══════════════════════════════════════════════════════════════════
# 리멤버 커리어 (Remember Career)
# ══════════════════════════════════════════════════════════════════

def crawl_remember(keyword):
    jobs = []
    try:
        enc = urllib.parse.quote(keyword)
        for api_url in [
            f"https://career.rememberapp.co.kr/api/v1/jobs?keyword={enc}&page=1&size=30",
            f"https://career.rememberapp.co.kr/api/v1/jobs?q={enc}&page=1&size=30",
            f"https://career.rememberapp.co.kr/api/jobs?keyword={enc}&page=1&limit=30",
            f"https://api.rememberapp.co.kr/v1/career/jobs?keyword={enc}&page=1",
            f"https://api.rememberapp.co.kr/career/v1/jobs?keyword={enc}&page=1",
        ]:
            try:
                d = fetch_json(api_url, referer="https://career.rememberapp.co.kr/",
                               headers={"Accept": "application/json"})
                items = (d.get("data") or d.get("jobs") or d.get("positions") or
                         d.get("result", {}).get("jobs") or [])
                if isinstance(items, dict):
                    items = items.get("list") or items.get("items") or []
                for job in items:
                    title   = (job.get("title") or job.get("jobTitle") or
                               job.get("positionName") or "")
                    company = (job.get("companyName") or job.get("company", {}).get("name", "")
                               if isinstance(job.get("company"), dict) else job.get("company",""))
                    job_id  = job.get("id","") or job.get("jobId","")
                    url     = (job.get("url","") or
                               f"https://career.rememberapp.co.kr/jobs/{job_id}" if job_id
                               else "https://career.rememberapp.co.kr/jobs")
                    if title:
                        jobs.append(make_job(title, company, url, "리멤버", keyword))
                if jobs:
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"  ⚠️  리멤버[{keyword}]: {e}")
    return jobs


# ══════════════════════════════════════════════════════════════════
# 잡플래닛 (Jobplanet)
# ══════════════════════════════════════════════════════════════════

def crawl_jobplanet(keyword):
    jobs = []
    try:
        enc = urllib.parse.quote(keyword)
        for api_url in [
            f"https://www.jobplanet.co.kr/job_postings/search.json?q={enc}&page=1",
            f"https://www.jobplanet.co.kr/job_postings.json?q={enc}&page=1",
            f"https://www.jobplanet.co.kr/api/v1/job_postings?keyword={enc}&page=1",
            f"https://api.jobplanet.co.kr/v1/jobs/search?keyword={enc}&page=1",
        ]:
            try:
                d = fetch_json(api_url, referer="https://www.jobplanet.co.kr/",
                               headers={"X-Requested-With": "XMLHttpRequest",
                                        "Accept": "application/json"})
                items = (d.get("job_postings") or d.get("data") or
                         d.get("result", {}).get("job_postings") or
                         d.get("jobs") or [])
                for job in items:
                    title   = (job.get("title") or job.get("jobTitle") or "")
                    company = (job.get("company", {}).get("name", "")
                               if isinstance(job.get("company"), dict)
                               else job.get("companyName",""))
                    job_id  = job.get("id","")
                    url     = (job.get("job_url","") or job.get("url","") or
                               f"https://www.jobplanet.co.kr/job_postings/{job_id}" if job_id
                               else "https://www.jobplanet.co.kr/jobs")
                    if title:
                        jobs.append(make_job(title, company, url, "잡플래닛", keyword))
                if jobs:
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"  ⚠️  잡플래닛[{keyword}]: {e}")
    return jobs


# ══════════════════════════════════════════════════════════════════
# 프로그래머스 커리어 — IT/PM 특화
# ══════════════════════════════════════════════════════════════════

def crawl_programmers(keyword):
    jobs = []
    try:
        enc = urllib.parse.quote(keyword)
        for base in [
            "https://career.programmers.co.kr",
            "https://programmers.co.kr",
        ]:
            for path in [
                f"/api/job_positions?order=recent&page=1&per_page=100&keyword={enc}",
                f"/api/job_positions?keyword={enc}&per_page=100",
            ]:
                try:
                    d = fetch_json(base+path, referer=base+"/",
                                   headers={"Accept":"application/json"})
                    items = d if isinstance(d, list) else d.get("job_positions", d.get("data", []))
                    for job in items:
                        title   = job.get("title","") or job.get("name","")
                        co      = job.get("company",{})
                        company = co.get("name","") if isinstance(co,dict) else job.get("companyName","")
                        job_id  = job.get("id","")
                        url     = (f"{base}/job_positions/{job_id}" if job_id
                                   else f"{base}/job_positions")
                        if title:
                            jobs.append(make_job(title, company, url, "프로그래머스", keyword))
                    if jobs: break
                except Exception:
                    continue
            if jobs: break
    except Exception as e:
        print(f"  ⚠️  프로그래머스[{keyword}]: {e}")
    return jobs


# ══════════════════════════════════════════════════════════════════
# 인크루트 (Incruit) — HTML 파싱
# ══════════════════════════════════════════════════════════════════

def crawl_incruit(keyword):
    jobs = []
    try:
        enc = urllib.parse.quote(keyword)
        html = fetch_html(
            f"https://search.incruit.com/list/search.asp?col=job&kw={enc}&page=1",
            referer="https://www.incruit.com/")
        # 공고 블록 파싱
        blocks = re.findall(
            r'class="[^"]*cell_mid[^"]*"(.*?)</li>', html, re.DOTALL)
        for block in blocks[:20]:
            title_m = re.search(r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)', block)
            if not title_m:
                title_m = re.search(r'class="[^"]*job_tit[^"]*"[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>([^<]+)', block, re.DOTALL)
            if title_m:
                href  = title_m.group(1).strip()
                title = clean(title_m.group(2))
                co_m  = re.search(r'class="[^"]*corp_name[^"]*"[^>]*>.*?([가-힣A-Za-z0-9\s\.&]+)<', block, re.DOTALL)
                company = clean(co_m.group(1)) if co_m else ""
                if not href.startswith("http"):
                    href = "https://www.incruit.com" + href
                if title and len(title) > 3:
                    jobs.append(make_job(title, company, href, "인크루트", keyword))
    except Exception as e:
        print(f"  ⚠️  인크루트[{keyword}]: {e}")
    return jobs


# ══════════════════════════════════════════════════════════════════
# 사람인 OAPI — access-key 있을 때만 동작
# 엔드포인트: https://oapi.saramin.co.kr/job-search
# Accept: application/json 헤더 필수
# ══════════════════════════════════════════════════════════════════

def crawl_saramin_api(keyword):
    if not SARAMIN_API_KEY:
        return []
    jobs = []
    try:
        enc = urllib.parse.quote(keyword)
        # ✅ 올바른 엔드포인트: /job-search (기존 /job/search 아님)
        d = fetch_json(
            f"https://oapi.saramin.co.kr/job-search"
            f"?access-key={SARAMIN_API_KEY}"
            f"&keywords={enc}"
            f"&loc_cd=101000"   # 서울
            f"&sort=pd"          # 최신 게시일 순
            f"&count=110",       # 최대값
            referer="https://www.saramin.co.kr/",
            headers={"Accept": "application/json"})  # JSON 응답 필수 헤더

        # 응답 구조: d["jobs"]["job"] = [{id, url, company, position, ...}]
        job_list = d.get("jobs", {}).get("job", [])
        if not isinstance(job_list, list):
            job_list = [job_list] if job_list else []

        for job in job_list:
            if not isinstance(job, dict): continue

            # title: position.title (XML→JSON 변환 시 문자열 또는 {"$": "...", ...})
            pos   = job.get("position", {})
            raw_t = pos.get("title", "")
            title = (raw_t.get("$", raw_t.get("#text","")) if isinstance(raw_t, dict)
                     else str(raw_t)).strip()

            # company: company.name (동일 구조)
            raw_c = job.get("company", {}).get("name", "")
            company = (raw_c.get("$", raw_c.get("#text","")) if isinstance(raw_c, dict)
                       else str(raw_c)).strip()

            url = job.get("url", "").strip()
            if not url.startswith("http"):
                jid = str(job.get("id",""))
                url = (f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={jid}"
                       if jid else "https://www.saramin.co.kr")

            # 마감일
            deadline = ""
            exp = job.get("expiration-date","") or job.get("expiration-timestamp","")
            if exp and isinstance(exp, str) and "T" in exp:
                deadline = exp.split("T")[0]

            if title:
                j = make_job(title, company, url, "사람인OAPI", keyword)
                j["deadline"] = deadline
                jobs.append(j)

    except Exception as e:
        print(f"  ⚠️  사람인OAPI[{keyword}]: {e}")
    return jobs


# ══════════════════════════════════════════════════════════════════
# Playwright — JS 렌더링 사이트 크롤러
# 전략: 페이지 로드 중 모든 JSON 응답 캡처 → title 키 재귀 탐색
# ══════════════════════════════════════════════════════════════════

PW_KEYWORDS = DEFAULT_TRACKER_KEYWORDS[:5] + ["서비스 기획", "전략기획", "기획", "PM", "LLM"]
PW_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# 채용 공고의 title에 해당하는 키 이름들
_JOB_TITLE_KEYS = {"title","jobTitle","recrtTitle","positionName","recruitTitle",
                   "position","job_title","noticeName","offerTitle","postingTitle"}
_JOB_ID_KEYS    = {"id","jobId","recIdx","positionId","jobOpeningId","postingId","seq"}
_JOB_CO_KEYS    = {"companyName","company_name","corpName","enterpriseName"}
_SKIP_TITLE_VAL = {"채용","공고","지원","커리어","career","jobs","recruit","home"}


def _extract_jobs_from_json(data, depth=0):
    """JSON 응답 어디서든 채용 공고 항목 재귀 탐색"""
    if depth > 6: return []
    results = []
    if isinstance(data, list):
        for item in data:
            results.extend(_extract_jobs_from_json(item, depth+1))
    elif isinstance(data, dict):
        has_title = any(k in data for k in _JOB_TITLE_KEYS)
        if has_title:
            results.append(data)
        else:
            for v in data.values():
                if isinstance(v, (dict, list)):
                    results.extend(_extract_jobs_from_json(v, depth+1))
    return results


def _parse_job_item(item, site_label, site_url, keyword=""):
    """채용 공고 dict → make_job 표준 형식"""
    title = ""
    for k in _JOB_TITLE_KEYS:
        if item.get(k):
            title = str(item[k]).strip(); break
    if not title or len(title) < 3: return None
    if any(v in title.lower() for v in _SKIP_TITLE_VAL): return None

    jid = ""
    for k in _JOB_ID_KEYS:
        if item.get(k):
            jid = str(item[k]); break

    company = ""
    for k in _JOB_CO_KEYS:
        if item.get(k):
            company = str(item[k]); break
    if not company and isinstance(item.get("company"), dict):
        company = item["company"].get("name","")

    url = item.get("url","") or item.get("href","") or item.get("applyUrl","") or site_url
    return make_job(title, company, url, site_label, keyword)


def _pw_load_all_json(page, goto_url, timeout=28000):
    """페이지 로드하며 모든 JSON 응답 수집 (JS 파일 제외, API 응답만)"""
    captured = []
    def on_resp(resp):
        ct   = resp.headers.get("content-type","")
        url  = resp.url
        # JS 파일 제외 (cdn, bundle, chunk, .js 확장자 등)
        skip_patterns = [".js?", ".js ", "/static/", "/assets/",
                         "chunk", "bundle", "vendor", "analytics", "gtm",
                         "google-analytics", "amplitude", "mixpanel"]
        if any(p in url.lower() for p in skip_patterns):
            return
        if resp.status == 200 and ("json" in ct):
            try:
                d = resp.json()
                if isinstance(d, (dict, list)):
                    captured.append(d)
            except: pass
    page.on("response", on_resp)
    try:
        page.goto(goto_url, wait_until="networkidle", timeout=timeout)
        page.wait_for_timeout(2000)   # 비동기 추가 로드 대기
    except Exception: pass
    page.remove_listener("response", on_resp)
    return captured


def _pw_crawl_with_search(page, base_url, site_label, keywords=None):
    """검색 페이지 순회하며 JSON 수집"""
    if keywords is None: keywords = PW_KEYWORDS
    jobs, seen = [], set()
    for kw in keywords:
        enc = urllib.parse.quote(kw)
        for goto in [
            f"{base_url}?query={enc}&tab=job",
            f"{base_url}?keyword={enc}",
            f"{base_url}?searchKeyword={enc}",
            f"{base_url}?q={enc}",
            base_url,
        ]:
            all_json = _pw_load_all_json(page, goto)
            for data in all_json:
                if not isinstance(data, (dict, list)): continue
                for item in _extract_jobs_from_json(data):
                    if not isinstance(item, dict): continue
                    j = _parse_job_item(item, site_label, base_url, kw)
                    if j:
                        key = j["url"] or (j["title"]+j.get("company",""))
                        if key not in seen:
                            seen.add(key); jobs.append(j)
            if jobs: break
        if jobs: break
    return jobs


# ── 원티드 ───────────────────────────────────────────────────────

def _pw_crawl_wanted(page):
    jobs, seen = [], set()
    for kw in PW_KEYWORDS:
        enc = urllib.parse.quote(kw)
        all_json = _pw_load_all_json(
            page, f"https://www.wanted.co.kr/search?query={enc}&tab=job")
        for data in all_json:
            if not isinstance(data, dict): continue
            # 원티드 응답: {"data": [{id, position, company_name, ...}]}
            raw = data.get("data", [])
            # data 키가 dict일 수도 있음: {"data": {"jobs": [...]}}
            if isinstance(raw, dict):
                raw = (raw.get("jobs") or raw.get("positions") or
                       raw.get("data") or raw.get("list") or [])
            if not isinstance(raw, list):
                raw = []
            for job in raw:
                if not isinstance(job, dict): continue
                jid = str(job.get("id",""))
                if jid in seen: continue
                seen.add(jid)
                title   = job.get("position","") or job.get("title","")
                co      = job.get("company",{})
                company = job.get("company_name","") or (co.get("name","") if isinstance(co,dict) else "")
                url     = f"https://www.wanted.co.kr/wd/{jid}" if jid else "https://www.wanted.co.kr"
                if title: jobs.append(make_job(title, company, url, "원티드", kw))
            # 원티드 구조 아니면 범용 탐색
            if not raw:
                for item in _extract_jobs_from_json(data):
                    j = _parse_job_item(item, "원티드", "https://www.wanted.co.kr", kw)
                    if j:
                        key = j["url"] or j["title"]
                        if key not in seen:
                            seen.add(key); jobs.append(j)
    return jobs


# ── 점핏 ─────────────────────────────────────────────────────────

def _pw_crawl_jumpit(page):
    jobs, seen = [], set()
    for kw in PW_KEYWORDS:
        enc = urllib.parse.quote(kw)
        all_json = _pw_load_all_json(
            page, f"https://www.jumpit.co.kr/search?keyword={enc}")
        for data in all_json:
            if not isinstance(data, dict): continue
            result = data.get("result", {})
            if not isinstance(result, dict): result = {}
            positions = result.get("positions", [])
            if not isinstance(positions, list): positions = []
            for pos in positions:
                if not isinstance(pos, dict): continue
                pid     = str(pos.get("id",""))
                if pid in seen: continue
                seen.add(pid)
                title   = pos.get("title","")
                company = pos.get("companyName","")
                url     = f"https://www.jumpit.co.kr/position/{pid}"
                if title: jobs.append(make_job(title, company, url, "점핏", kw))
            if not positions:
                for item in _extract_jobs_from_json(data):
                    j = _parse_job_item(item, "점핏", "https://www.jumpit.co.kr", kw)
                    if j:
                        key = j["url"] or j["title"]
                        if key not in seen:
                            seen.add(key); jobs.append(j)
    return jobs


# ── 리멤버 ───────────────────────────────────────────────────────

def _pw_crawl_remember(page):
    return _pw_crawl_with_search(
        page, "https://career.rememberapp.co.kr/jobs", "리멤버", PW_KEYWORDS[:5])


# ── 삼성 커리어스 ─────────────────────────────────────────────────

def _pw_crawl_samsung(page):
    jobs, seen = [], set()
    kw_list = ["AI", "기획", "전략", "디지털", "TPM", ""]
    for kw in kw_list:
        enc = urllib.parse.quote(kw)
        for url in [
            f"https://www.samsungcareers.com/rec/retrieveExternalRecruitList.do?searchKeyword={enc}&pageIndex=1",
            f"https://career.samsung.com/main/html/kor/recruitMain.html",
        ]:
            all_json = _pw_load_all_json(page, url, timeout=30000)
            for data in all_json:
                for item in _extract_jobs_from_json(data):
                    j = _parse_job_item(item, "삼성커리어스", "https://www.samsungcareers.com")
                    if j:
                        key = j["url"] or j["title"]
                        if key not in seen:
                            seen.add(key); jobs.append(j)
            if jobs: break
        if jobs: break

    # JSON 실패 시 HTML에서 직접 파싱
    if not jobs:
        try:
            page.goto("https://www.samsungcareers.com/", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
            html = page.content()
            # 공고 링크 패턴
            for m in re.finditer(
                    r'href=["\']([^"\']*retrieveExternalRecruit[^"\']+recIdx=(\d+)[^"\']*)["\'][^>]*>[^<]*<[^>]+>([가-힣A-Za-z0-9 \(\)\[\]\/\-\_\.]{4,80})',
                    html):
                href, rid, title = m.group(1), m.group(2), clean(m.group(3))
                if not href.startswith("http"):
                    href = "https://www.samsungcareers.com" + href
                key = href or title
                if key not in seen:
                    seen.add(key)
                    jobs.append(make_job(title, "삼성", href, "삼성커리어스"))
        except Exception:
            pass
    return jobs


# ── SK 커리어스 ───────────────────────────────────────────────────

def _pw_crawl_sk(page):
    jobs = _pw_crawl_with_search(
        page, "https://www.skcareers.com", "SK커리어스",
        ["AI", "기획", "전략", "디지털", "TPM"])
    # Fallback: 목록 페이지 직접
    if not jobs:
        jobs = _pw_crawl_with_search(
            page, "https://careers.sk.com", "SK커리어스",
            ["AI", "기획", ""])
    return jobs


# ── CJ그룹 커리어스 ───────────────────────────────────────────────

def _pw_crawl_cj(page):
    jobs = _pw_crawl_with_search(
        page, "https://careers.cj.net", "CJ커리어스",
        ["AI", "기획", "전략", "디지털", ""])
    # Fallback: CJ그룹 통합 채용 다른 도메인
    if not jobs:
        jobs = _pw_crawl_with_search(
            page, "https://recruit.cj.net", "CJ커리어스",
            ["AI", "기획", ""])


# ── 네이버 (PW fallback) ──────────────────────────────────────────

def _pw_crawl_naver(page):
    return _pw_crawl_with_search(
        page, "https://recruit.navercorp.com", "네이버",
        ["AI", "기획", "전략", "TPM", "LLM"])


# ── Meta (PW fallback) ────────────────────────────────────────────

def _pw_crawl_meta(page):
    jobs, seen = [], set()
    for url in [
        "https://www.metacareers.com/jobs?offices[]=Seoul",
        "https://www.metacareers.com/jobs?q=korea",
    ]:
        all_json = _pw_load_all_json(page, url, timeout=30000)
        for data in all_json:
            if not isinstance(data, (dict, list)): continue
            for item in _extract_jobs_from_json(data):
                if not isinstance(item, dict): continue
                j = _parse_job_item(item, "Meta", "https://www.metacareers.com/jobs")
                if j:
                    key = j["url"] or j["title"]
                    if key not in seen:
                        seen.add(key); jobs.append(j)
        if jobs: break
    return jobs


# ── 통합 실행 ────────────────────────────────────────────────────

def run_playwright_crawlers():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ℹ️  Playwright 미설치 (pip3 install playwright && playwright install chromium)")
        return []

    print(f"\n  🌐  Playwright 크롤링 시작...")
    all_jobs = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox","--disable-setuid-sandbox",
                      "--disable-blink-features=AutomationControlled"])
            ctx = browser.new_context(
                user_agent=PW_UA, locale="ko-KR", timezone_id="Asia/Seoul",
                extra_http_headers={"Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"})

            sites = {
                "원티드":      (ctx.new_page(), _pw_crawl_wanted),
                "점핏":        (ctx.new_page(), _pw_crawl_jumpit),
                "리멤버":      (ctx.new_page(), _pw_crawl_remember),
                "네이버(PW)":  (ctx.new_page(), _pw_crawl_naver),
                "Meta(PW)":    (ctx.new_page(), _pw_crawl_meta),
                "삼성커리어스": (ctx.new_page(), _pw_crawl_samsung),
                "SK커리어스":   (ctx.new_page(), _pw_crawl_sk),
                "CJ커리어스":   (ctx.new_page(), _pw_crawl_cj),
            }

            results = {}
            for name, (pg, fn) in sites.items():
                try:
                    print(f"  ...PW/{name}")
                    results[name] = fn(pg)
                except Exception as e:
                    print(f"  ⚠️  PW/{name}: {e}")
                    results[name] = []

            ctx.close()
            browser.close()

            for name, jobs in results.items():
                all_jobs.extend(jobs)
                if jobs:
                    print(f"  ✅  PW/{name} → {len(jobs)}건")
                else:
                    print(f"  ○   PW/{name} → 0건")

    except Exception as e:
        print(f"  ⚠️  Playwright 실행 오류: {e}")
    return all_jobs


# ══════════════════════════════════════════════════════════════════
# 크롤러 목록 & 메인
# ══════════════════════════════════════════════════════════════════

CRAWLERS = [
    # 네카라쿠배당토
    (crawl_naver,       "네이버"),
    (crawl_kakao,       "카카오"),
    (crawl_kakaobank,   "카카오뱅크"),
    (crawl_coupang,     "쿠팡"),
    (crawl_woowa,       "배달의민족"),
    (crawl_daangn,      "당근마켓"),
    (crawl_toss,        "토스"),
    (crawl_tossbank,    "토스뱅크"),
    (crawl_line,        "라인"),
    (crawl_zigbang,     "직방"),
    (crawl_yanolja,     "야놀자"),
    (crawl_dunamu,      "두나무"),
    (crawl_moloco,      "몰로코"),
    (crawl_sendbird,    "센드버드"),
    (crawl_musinsa,     "무신사"),
    (crawl_channel_talk,"채널톡"),
    # 금융
    (crawl_hyundai_card,"현대카드"),
    (crawl_kb,          "KB금융"),
    (crawl_shinhan,     "신한금융"),
    (crawl_hana,        "하나금융"),
    (crawl_woori,       "우리금융"),
    (crawl_nh,          "농협"),
    (crawl_hanwha_life, "한화생명"),
    (crawl_samsung_life,"삼성생명"),
    (crawl_axa,         "AXA손해보험"),
    # 대기업
    (crawl_hyundai_motor,"현대자동차"),
    (crawl_skt,         "SK텔레콤"),
    (crawl_lgcns,       "LG CNS"),
    (crawl_oliveyoung,  "올리브영"),
    (crawl_ssg,         "신세계/SSG"),
    (crawl_samsung,     "삼성커리어스"),
    (crawl_sk_careers,  "SK커리어스"),
    (crawl_cj,          "CJ그룹"),
    # 글로벌
    (crawl_google,      "Google"),
    (crawl_microsoft,   "Microsoft"),
    (crawl_meta,        "Meta"),
    (crawl_openai,      "OpenAI"),
]

# 플랫폼 검색 키워드 (원티드/점핏/리멤버/잡플래닛/프로그래머스/인크루트 공통)
BOARD_KEYWORDS = DEFAULT_TRACKER_KEYWORDS[:]
SARAMIN_KEYWORDS = DEFAULT_TRACKER_KEYWORDS[:5]


def run(custom_keywords=None):
    config = load_tracker_config()
    chosen_keywords = custom_keywords or config["keywords"]
    board_keywords, saramin_keywords = build_keyword_sets(chosen_keywords)
    pw_keywords = board_keywords[:5]

    global BOARD_KEYWORDS, SARAMIN_KEYWORDS, PW_KEYWORDS
    BOARD_KEYWORDS = board_keywords
    SARAMIN_KEYWORDS = saramin_keywords
    PW_KEYWORDS = pw_keywords

    print(f"\n{'─'*58}")
    print(f"  🔎 이주영 채용공고 크롤러 v8")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    saramin_status = "✅ key 있음" if SARAMIN_API_KEY else "⚠️  key 없음(oapi.saramin.co.kr)"
    print(f"  사람인OAPI: {saramin_status}")
    print(f"  키워드: {', '.join(board_keywords)}")
    print(f"{'─'*58}\n")

    all_results = []

    # ── 1. HTTP 기반 병렬 크롤러 ──────────────────────────────
    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = {}
        for fn, label in CRAWLERS:
            futures[ex.submit(fn)] = label
        # 원티드/점핏 직군 ID 방식 (HTTP) — Playwright 없을 때 fallback
        futures[ex.submit(crawl_wanted)] = "원티드(HTTP)"
        futures[ex.submit(crawl_jumpit)] = "점핏(HTTP)"
        # 키워드 기반 플랫폼
        for kw in BOARD_KEYWORDS:
            futures[ex.submit(crawl_remember,    kw)] = f"리멤버/{kw}"
            futures[ex.submit(crawl_jobplanet,   kw)] = f"잡플래닛/{kw}"
            futures[ex.submit(crawl_programmers, kw)] = f"프로그래머스/{kw}"
            futures[ex.submit(crawl_rocketpunch, kw)] = f"로켓펀치/{kw}"
            futures[ex.submit(crawl_incruit,     kw)] = f"인크루트/{kw}"
        for kw in SARAMIN_KEYWORDS:
            futures[ex.submit(crawl_saramin,     kw)] = f"사람인/{kw}"
            futures[ex.submit(crawl_saramin_rss, kw)] = f"사람인RSS/{kw}"
            futures[ex.submit(crawl_saramin_api, kw)] = f"사람인OAPI/{kw}"

        for future in as_completed(futures, timeout=90):
            label = futures[future]
            try:
                jobs = future.result()
                all_results.extend(jobs)
                cnt = len(jobs)
                if cnt > 0:
                    print(f"  ✅  {label} → {cnt}건")
                else:
                    print(f"  ○   {label} → 0건")
            except Exception as e:
                print(f"  ❌  {label} 실패: {e}")

    # ── 2. Playwright 크롤러 (원티드·점핏·리멤버 JS 렌더링) ──
    pw_jobs = run_playwright_crawlers()
    all_results.extend(pw_jobs)

    # URL 중복 제거
    seen, unique = set(), []
    for job in all_results:
        key = job["url"] or job["title"] + job.get("company","")
        if key and key not in seen:
            seen.add(key); unique.append(job)

    unique.sort(key=lambda j: j["score"], reverse=True)

    output = {
        "crawled_at": datetime.now().isoformat(),
        "schedule_label": config["schedule_label"],
        "sources": config["sources"],
        "keywords": board_keywords,
        "total":  len(unique),
        "green":  sum(1 for j in unique if j["verdict"]=="green"),
        "orange": sum(1 for j in unique if j["verdict"]=="orange"),
        "red":    sum(1 for j in unique if j["verdict"]=="red"),
        "jobs":   unique,
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2))

    print(f"\n{'─'*58}")
    print(f"  📊 총 {len(unique)}건 수집")
    print(f"  ✅  적극지원: {output['green']}건")
    print(f"  🔶  조건부:  {output['orange']}건")
    print(f"  ❌  낮음:    {output['red']}건")
    print(f"  💾  {OUTPUT_PATH}")
    print(f"{'─'*58}\n")
    return output


if __name__ == "__main__":
    arg_keywords = None
    if len(sys.argv) >= 3 and sys.argv[1] == "--keywords-json":
        try:
            parsed = json.loads(sys.argv[2])
            if isinstance(parsed, list):
                arg_keywords = [str(k).strip() for k in parsed if str(k).strip()]
        except Exception:
            arg_keywords = None
    run(arg_keywords)
