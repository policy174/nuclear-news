"""사내 신문스크랩 카톡방 시드 → 원문 공개 기사 역추적 합류.

입력 `scrap_seeds.json` 은 PC 로컬 스크립트(tools/scrap_seed_push.py)가
카톡방의 "N월 N일 조간/석간스크랩 보고" 텍스트에서 매체명·제목만 뽑아
GitHub API 로 커밋한다 — 스크랩 PDF·지면 스캔은 사내 자료라 절대 싣지
않는다(저작권·사규). 여기서는 미해결 시드를 네이버 뉴스 API 로 검색해
**공개 원문 URL** 을 찾고, 찾은 것만 news_bot article dict 로 합류시킨다.

지면 제목과 온라인 제목은 자주 다르다(지면은 축약·의역) — 완전 일치 대신
토큰 겹침으로 판정하고, 못 찾으면 다음 크롤에서 재시도한다(백오프·만료).
해석 장부는 state(sent.json)의 "scrap_seeds" 키에 산다 — 별도 커밋 불필요.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEEDS_FILE = Path("scrap_seeds.json")
SEED_MAX_AGE_DAYS = 5      # 이보다 오래된 시드는 재시도 안 함(뉴스 신선도 소진)
SEED_MAX_TRIES = 6         # 백오프 재시도 상한
SEED_RETRY_HOURS = 5       # 재시도 최소 간격 (매시간 크롤이 연타하지 않게)
MATCH_THRESHOLD = 0.5      # 시드 제목 토큰이 후보 제목에 절반 이상 있으면 같은 기사

KST = timezone(timedelta(hours=9))

# "1.[헤럴드경제 027면] 전기에도 색깔을 입혀보자 (민병권 …)" 꼴. 면 번호는
# 없을 수도 있다. 끝의 괄호 꼬리(저자·직함·'종합' 류)는 제목에서 뗀다 —
# 네이버 API 가 추가 단어를 AND 결합해 쿼리를 죽이고(2026-08-31 실측:
# 저자 포함 검색 0건, 제외하면 1위 적중), 토큰 포함률도 희석된다.
_REPORT_LINE = re.compile(r"^\s*\d+\.\s*\[(?P<pub>[^\]]+?)(?:\s+\d+면)?\]\s*(?P<title>\S.*)$")
_TITLE_TAIL = re.compile(r"\s*\([^()]*\)\s*$")
_REPORT_HEADER = re.compile(r"(?P<month>\d{1,2})월\s*(?P<day>\d{1,2})일\s*(조간|석간)?\s*스크랩\s*보고")


def parse_scrap_report(text: str, year: int) -> list[dict]:
    """스크랩 보고 텍스트 → [{date, publisher, title}]. 보고 형식이 아니면 []."""
    header = _REPORT_HEADER.search(text)
    if not header:
        return []
    try:
        date = datetime(year, int(header.group("month")), int(header.group("day"))).date().isoformat()
    except ValueError:
        return []
    seeds = []
    for line in text.splitlines():
        m = _REPORT_LINE.match(line)
        if m:
            title = _TITLE_TAIL.sub("", m.group("title")).strip()
            if not title:
                continue
            seeds.append({"date": date,
                          "publisher": m.group("pub").strip(),
                          "title": title})
    return seeds


def seed_key(seed: dict) -> str:
    raw = f"{seed.get('date')}|{seed.get('publisher')}|{seed.get('title')}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]{2,}")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text or ""))


def title_overlap(seed_title: str, candidate_title: str) -> float:
    """시드 제목 토큰 중 후보 제목에 든 비율(0~1). 방향이 있는 포함률 —
    온라인 제목이 더 길어도(부제 등) 지면 제목이 안에 있으면 잡힌다."""
    seed_tokens = _tokens(seed_title)
    if not seed_tokens:
        return 0.0
    return len(seed_tokens & _tokens(candidate_title)) / len(seed_tokens)


def load_seeds() -> list[dict]:
    try:
        raw = json.loads(SEEDS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return raw if isinstance(raw, list) else []


def fetch_scrap_seed_articles(state: dict) -> list[dict]:
    """미해결 시드를 네이버로 역추적해 article dict 리스트로 반환.

    state["scrap_seeds"] 가 장부: {key: {status, tries, last_tried, ...}}.
    state 는 호출자가 save_state 로 커밋하므로 여기선 dict 만 갱신한다.
    """
    seeds = load_seeds()
    if not seeds:
        return []

    # 순환 import 회피 — email_ingest 와 같은 lazy import 패턴
    from news_bot import article_seen, get_domain, search_naver, source_profile, strip_html, url_hash

    ledger = state.setdefault("scrap_seeds", {})
    now = datetime.now(timezone.utc)
    cutoff_date = (datetime.now(KST) - timedelta(days=SEED_MAX_AGE_DAYS)).date().isoformat()
    # 장부도 시드와 같은 수명으로 청소 (sent 청소는 save_state 가 하지만 이 키는 안 본다)
    for key in [k for k, v in ledger.items() if (v.get("seed_date") or "") < cutoff_date]:
        del ledger[key]

    articles: list[dict] = []
    resolved = skipped = 0
    for seed in seeds:
        if (seed.get("date") or "") < cutoff_date:
            continue
        key = seed_key(seed)
        row = ledger.get(key) or {"tries": 0, "seed_date": seed.get("date")}
        if row.get("status") == "gave_up":
            continue
        if row.get("status") == "resolved":
            # 해결됐어도 기사가 파이프라인에 안착(sent)할 때까지 재주입한다 —
            # 429 쿼터 소진 크롤은 큐레이션을 건너뛰며 sent 마킹을 안 하는데,
            # 시드 기사는 키워드에 안 걸려 정규 재수집 경로가 없다(2026-08-31
            # 실측: 해결 1건이 429 시각과 겹쳐 소리 없이 유실). 재검색은 없음.
            link = row.get("link")
            if link and not article_seen(state, link):
                domain = get_domain(link)
                articles.append({
                    "hash": url_hash(link),
                    "title": row.get("title") or seed["title"],
                    "description": row.get("description", ""),
                    "link": link,
                    "pub": now,
                    "matched": "사내스크랩",
                    "score": 10,
                    "domain": domain,
                    "publisher": source_profile(domain).get("publisher", "") or seed.get("publisher", ""),
                    "feed": "정책",
                })
            continue
        if row["tries"] >= SEED_MAX_TRIES:
            row["status"] = "gave_up"
            ledger[key] = row
            continue
        last = row.get("last_tried")
        if last and (now - datetime.fromisoformat(last)) < timedelta(hours=SEED_RETRY_HOURS):
            skipped += 1
            continue

        row["tries"] += 1
        row["last_tried"] = now.isoformat()
        ledger[key] = row
        try:
            items = search_naver(seed["title"])
        except Exception as e:  # noqa: BLE001 — 시드 하나가 수집을 못 막는다
            print(f"  ! [scrap_seed] '{seed['title'][:30]}' 검색 실패: {type(e).__name__}")
            continue

        best, best_score = None, 0.0
        for item in items:
            link = item.get("originallink") or item.get("link")
            if not link:
                continue
            score = title_overlap(seed["title"], strip_html(item.get("title", "")))
            if score > best_score:
                best, best_score = item, score
        if not best or best_score < MATCH_THRESHOLD:
            continue

        link = best.get("originallink") or best.get("link")
        row["status"] = "resolved"
        row["link"] = link
        # 재주입용 스냅샷 — 429 로 이번 크롤에서 유실돼도 다음 크롤이 재검색
        # 없이 다시 넣을 수 있게 한다.
        row["title"] = strip_html(best.get("title", "")) or seed["title"]
        row["description"] = strip_html(best.get("description", ""))
        if article_seen(state, link):
            resolved += 1  # 봇이 이미 수집한 기사 — 시드 목적 달성, 주입 불필요
            continue
        domain = get_domain(link)
        articles.append({
            "hash": url_hash(link),
            "title": strip_html(best.get("title", "")) or seed["title"],
            "description": strip_html(best.get("description", "")),
            "link": link,
            "pub": now,
            "matched": "사내스크랩",
            "score": 10,  # 홍보실 큐레이션 통과 = 신뢰 seed 가중 (email_ingest 와 동일)
            "domain": domain,
            "publisher": source_profile(domain).get("publisher", "") or seed.get("publisher", ""),
            "feed": "정책",
        })
        resolved += 1

    pending = sum(1 for v in ledger.values() if v.get("status") not in ("resolved", "gave_up"))
    print(f"[scrap_seed] 시드 {len(seeds)}건: 해결 {resolved} / 신규 주입 {len(articles)} / 대기 {pending} (백오프 {skipped})")
    return articles
