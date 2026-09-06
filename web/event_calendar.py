"""앞으로 30일 일정 — 기사 문장에서 날짜 절(clause)을 뽑아 달력 재료를 만든다.

계약(라이브 v2 실측과 동일):
  build(articles, today) -> {
    start, end, days, events[], month_notes[], dropped{out_of_window}
  }
  event = { date, end_date, kind(point|deadline|range), label, clause,
            origin:"clause", hash, story_id, issue_id, title, url, publisher,
            topics, source_kind:"news", sources[], first_seen, source_count, id }

원칙:
- 날짜와 이름을 **같은 문장**에서 짝짓는다. 날짜는 절에서, 이름은 제목에서
  따로 가져오면 "9월 1일 · 8월 25일 행사"가 된다(v2 머리말의 실사고).
- LLM 0회. 정규식이 못 읽는 문장은 조용히 버린다 — 빈 칸은 고장이 아니라 사실.
- '9월 중'처럼 달까지만 나온 일정은 events 가 아니라 month_notes 로 — 날짜
  칸에 넣는 순간 그 달 1일 일정이 된다.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

WINDOW_DAYS = 30
MAX_EVENTS = 60          # 화면 상한은 프런트 몫이지만 페이로드 폭주는 여기서 막는다
_LABEL_MAX = 40
_LABEL_MIN = 4           # '이 회' 같은 부스러기는 라벨이 아니다 — 제목으로 물러난다
# 이보다 긴 '기간'은 일정이 아니라 상태다(위촉 임기 3년, 공급계약 6년) —
# 달력에 세우면 창 내내 '진행 중' 칩이 붙는 배경이 된다.
_RANGE_MAX_DAYS = 120


def _date_pat(prefix: str) -> str:
    return (rf"(?:(?P<{prefix}y>\d{{4}})년\s*)?"
            rf"(?P<{prefix}m>\d{{1,2}})월\s*(?P<{prefix}d>\d{{1,2}})일")


# 순서가 판정이다: 범위 → 범위 축약형 → 마감 → 단일 날짜.
RANGE_RE = re.compile(_date_pat("a") + r"\s*(?:부터|~|∼|-)\s*" + _date_pat("b") + r"\s*까지")
# "9월 8일부터 12일까지" — 끝날짜가 달을 상속한다. 여기를 놓치면 12일이
# 다른 달로 붙는다(v2 의 알려진 오파싱).
RANGE_DAY_RE = re.compile(_date_pat("a") + r"\s*(?:부터|~|∼)\s*(?P<bd>\d{1,2})일\s*까지")
# "27일부터 9월 10일까지" — 시작일에 달이 없다. 발행일에 가장 가까운 달로
# 읽는다(8월 기사의 '11일부터'는 8/11). 이걸 안 잡으면 마감(까지)만 잡혀
# 시작이 사라지고, '27일부터'가 라벨에 배경처럼 남는다.
DAY_RANGE_RE = re.compile(r"(?<![월\d])(?P<ad>\d{1,2})일\s*(?:부터|~|∼)\s*"
                          + _date_pat("b") + r"\s*까지")
DEADLINE_RE = re.compile(_date_pat("a") + r"\s*까지")
POINT_RE = re.compile(_date_pat("a"))
MONTH_RE = re.compile(r"(?:(?P<y>\d{4})년\s*)?(?P<m>\d{1,2})월\s*(?:중|초|말)(?!\S)")

# 일정으로 셀 자격 — 이 낱말이 문장에 없으면 날짜는 일정이 아니라 배경이다
# ("지난 8월 30일 기준" 따위). 범위·마감(부터/까지)은 그 자체가 일정 신호라
# 라벨을 못 찾을 때만 제목으로 물러난다.
EVENT_KW = re.compile(
    r"(입법예고|공청회|설명회|토론회|간담회|공람|접수|공모|공고|모집|신청|마감|투표|"
    r"표결|발표|개최|개막|시행|발효|착수|착공|준공|가동|재가동|정지|점검|정비|회의|"
    r"총회|세미나|포럼|심포지엄|웨비나|행사|방문|회담|협상|파업|집회|선고|심사|"
    r"심의|의결|입찰|제출|공개|출시|기념식|서명식|협약식)(?!률|율)")

# 라벨 머리에서 걷어내는 연결어 — 날짜를 지운 자리에 남는 부사들.
_LEAD_NOISE = re.compile(r"^(오는|이달|내달|지난|올해|내년|당초|현재|또한|이어|한편)\s*")
_TOKEN_NOISE = {"오는", "이달", "내달", "지난", "올해", "내년", "위해", "위한",
                "통해", "대상으로", "함께", "관련", "대한", "따라"}
_PARTICLE_TAIL = re.compile(
    r"(을|를|이|가|은|는|에|에서|으로|로|과|와|의|도|만)$")


def _sentences(text: object):
    for part in re.split(r"(?<=[.!?다음함임됨])\s+|\n+", str(text or "")):
        part = part.strip()
        if len(part) >= 8:
            yield part


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _resolve(groups: dict, prefix: str, anchor: date) -> date | None:
    """연도가 없으면 anchor(발행일 또는 시작일)에 가장 가까운 해로 읽는다.
    12월 기사 속 '1월 10일'은 이듬해로, 그제 일은 작년으로 돌지 않는다 —
    시제 분석 없이 창(window) 필터가 과거를 걸러낸다."""
    year = groups.get(prefix + "y")
    month = int(groups[prefix + "m"])
    day = int(groups[prefix + "d"])
    if year:
        return _valid(int(year), month, day)
    candidates = [c for off in (-1, 0, 1) if (c := _valid(anchor.year + off, month, day))]
    if not candidates:
        return None
    # 동률이면 미래 쪽 — 기사는 대개 앞을 말한다.
    return min(candidates, key=lambda c: (abs((c - anchor).days), (anchor - c).days))


def _strip_dates(sentence: str) -> str:
    out = RANGE_RE.sub(" ", sentence)
    out = RANGE_DAY_RE.sub(" ", out)
    out = DAY_RANGE_RE.sub(" ", out)
    out = DEADLINE_RE.sub(" ", out)
    out = POINT_RE.sub(" ", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def _clean_tokens(tokens: list[str], keep: int) -> list[str]:
    picked = [t for t in tokens if t not in _TOKEN_NOISE][-keep:]
    while picked and (_LEAD_NOISE.match(picked[0]) or picked[0] in _TOKEN_NOISE):
        picked = picked[1:]
    if picked:
        picked[-1] = _PARTICLE_TAIL.sub("", picked[-1]) or picked[-1]
    return picked


def _label_near(text: str, dm: re.Match, title: str) -> str:
    """사건 이름은 날짜 **바로 뒤**에서 먼저 찾는다 — "…까지 접수한다",
    "9월 11일 시행"처럼 한국어는 날짜 뒤에 사건 동사가 온다. 문장 끝의
    아무 키워드나 잡으면 같은 문장 뒤쪽의 다른 사건(…10월에 발표할 예정)이
    이 날짜의 이름이 된다. 뒤에 없을 때만 앞을 보고, 그마저 없으면 제목."""
    after = text[dm.end():dm.end() + 40]
    kw = EVENT_KW.search(after)
    if kw:
        subject = _clean_tokens(text[:dm.start()].split(), 3)
        label = " ".join(subject + [kw.group(1)])
    else:
        before = text[:dm.start()]
        match = None
        for match in EVENT_KW.finditer(before):
            pass
        if not match:
            return str(title or "")[:_LABEL_MAX].strip()
        head = before[:match.end()]
        tokens = head.split()
        if tokens:
            hit = EVENT_KW.search(tokens[-1])
            if hit:
                tokens[-1] = tokens[-1][:hit.end()]
        label = " ".join(_clean_tokens(tokens, 4))
    label = label.strip(" ·,‘’'\"“”()[]")
    if len(label) < _LABEL_MIN:
        label = str(title or "")
    return (label or str(title or ""))[:_LABEL_MAX].strip()


def _month_label(sentence: str, title: str) -> str:
    """'9월 중' 노트용 — 날짜 절이 없는 문장이라 문장 전체에서 마지막 키워드."""
    stripped = _LEAD_NOISE.sub("", _strip_dates(MONTH_RE.sub(" ", sentence)))
    match = None
    for match in EVENT_KW.finditer(stripped):
        pass
    if not match:
        return str(title or "")[:_LABEL_MAX].strip()
    tokens = stripped[:match.end()].split()
    if tokens:
        hit = EVENT_KW.search(tokens[-1])
        if hit:
            tokens[-1] = tokens[-1][:hit.end()]
    label = " ".join(_clean_tokens(tokens, 4)).strip(" ·,‘’'\"“”()[]")
    return (label or str(title or ""))[:_LABEL_MAX].strip()


def _norm(label: str) -> str:
    return re.sub(r"[\s·,.'\"‘’“”()\[\]]", "", label)[:12]


def _extract(sentence: str, pub: date, title: str):
    """문장 하나에서 (kind, start, end, label) 후보를 최대 2개 낸다."""
    found = []
    rest = sentence
    for _ in range(2):
        m = RANGE_RE.search(rest)
        if m:
            start = _resolve(m.groupdict(), "a", pub)
            end = _resolve(m.groupdict(), "b", start or pub)
            if start and end and end >= start:
                found.append(("range", start, end, _label_near(rest, m, title)))
            rest = rest[:m.start()] + " " + rest[m.end():]
            continue
        m = RANGE_DAY_RE.search(rest)
        if m:
            start = _resolve(m.groupdict(), "a", pub)
            if start:
                end = _valid(start.year, start.month, int(m.group("bd")))
                if end and end < start:  # "1월 30일부터 3일까지" — 다음 달로
                    roll = start.replace(day=1) + timedelta(days=32)
                    end = _valid(roll.year, roll.month, int(m.group("bd")))
                if end and end >= start:
                    found.append(("range", start, end, _label_near(rest, m, title)))
            rest = rest[:m.start()] + " " + rest[m.end():]
            continue
        m = DAY_RANGE_RE.search(rest)
        if m:
            end = _resolve(m.groupdict(), "b", pub)
            if end:
                day = int(m.group("ad"))
                candidates = []
                for off in (-1, 0, 1):
                    month_anchor = (pub.replace(day=1) + timedelta(days=off * 31)).replace(day=1)
                    c = _valid(month_anchor.year, month_anchor.month, day)
                    if c and c <= end:
                        candidates.append(c)
                if candidates:
                    start = min(candidates, key=lambda c: abs((c - pub).days))
                    found.append(("range", start, end, _label_near(rest, m, title)))
            rest = rest[:m.start()] + " " + rest[m.end():]
            continue
        m = DEADLINE_RE.search(rest)
        if m:
            day = _resolve(m.groupdict(), "a", pub)
            if day:
                found.append(("deadline", day, day, _label_near(rest, m, title)))
            rest = rest[:m.start()] + " " + rest[m.end():]
            continue
        m = POINT_RE.search(rest)
        if m:
            # '지난 9월 4일' — 명시적 과거 지칭은 일정이 아니다.
            lead = rest[max(0, m.start() - 3):m.start()]
            day = _resolve(m.groupdict(), "a", pub)
            if day and "지난" not in lead and EVENT_KW.search(sentence):
                found.append(("point", day, day, _label_near(rest, m, title)))
            rest = rest[:m.start()] + " " + rest[m.end():]
            continue
        break
    return found


def _merge_similar(rows: list[dict]) -> list[dict]:
    """같은 (날짜, 끝, 종류)의 라벨 변형을 접는다 — "영광 한빛원전 2호기 가동"과
    "한국수력원자력 한빛 2호기 가동"은 한 사건이다. 내용 토큰(2자 이상)의
    겹침이 작은 쪽 집합의 절반을 넘으면 같은 사건으로 본다.
    ponytail: 토큰 자카드 근사 — 오접합이 보이면 임계 상향이 첫 수단."""
    merged: list[dict] = []
    for row in rows:
        row_tokens = {t for t in row["label"].split() if len(t) >= 2}
        target = None
        for cand in merged:
            if (cand["date"], cand["end_date"], cand["kind"]) != (row["date"], row["end_date"], row["kind"]):
                continue
            cand_tokens = {t for t in cand["label"].split() if len(t) >= 2}
            overlap = row_tokens & cand_tokens
            if overlap and len(overlap) / max(1, min(len(row_tokens), len(cand_tokens))) >= 0.5:
                target = cand
                break
        if target is None:
            merged.append(row)
            continue
        for source in row["sources"]:
            if not any(s["hash"] == source["hash"] for s in target["sources"]):
                target["sources"].append(source)
        if row["first_seen"] < target["first_seen"]:
            target["first_seen"] = row["first_seen"]
        if not target["issue_id"] and row["issue_id"]:
            target["issue_id"] = row["issue_id"]
        # 더 짧은 라벨이 대개 군더더기(주어 조사)가 덜 붙은 쪽이다.
        # 단 official 타깃의 라벨은 기관이 적은 이름 그대로 둔다 — 기사 절에서
        # 깎은 라벨이 짧다고 공지 제목을 덮으면 공식 배지 옆에 남의 말이 선다.
        if target.get("origin") != "official" and len(row["label"]) < len(target["label"]):
            target["label"] = row["label"]
    return merged


def _official_row(item: dict) -> dict | None:
    """official_events.json 의 행 → 달력 이벤트 계약으로 코어스.
    date/end_date 가 ISO 가 아니면 버린다 — 스토어는 외부 스크레이퍼 산출이라
    빌드가 신뢰하지 않는다(파서가 개편에 깨진 날의 쓰레기 행 방어)."""
    day = _parse_pub(item.get("date"))
    end = _parse_pub(item.get("end_date")) or day
    label = str(item.get("label") or "").strip()
    if not day or not label or end < day:
        return None
    kind = item.get("kind") if item.get("kind") in ("point", "deadline", "range") else "point"
    notice_title = str(item.get("notice_title") or "")
    return {
        "date": day.isoformat(),
        "end_date": end.isoformat(),
        "kind": kind,
        "label": label,
        # 팝오버의 근거 문장 자리 — 공지는 문장이 따로 없어 공지 제목이 그 역할.
        "clause": notice_title or label,
        "origin": "official",
        "hash": str(item.get("hash") or ""),
        "story_id": "",
        "issue_id": "",
        "title": notice_title or label,
        "url": str(item.get("url") or ""),
        "publisher": str(item.get("publisher") or ""),
        "topics": item.get("topics") or [],
        "source_kind": "official",
        "time": str(item.get("time") or ""),
        "host": str(item.get("host") or ""),
        "organizer": str(item.get("organizer") or ""),
        "place": str(item.get("place") or ""),
        "source_id": str(item.get("source_id") or ""),
        "sources": [{
            "hash": str(item.get("hash") or ""),
            "story_id": "",
            "issue_id": "",
            "title": notice_title or label,
            "url": str(item.get("url") or ""),
            "publisher": str(item.get("publisher") or ""),
            "topics": item.get("topics") or [],
            "source_kind": "official",
        }],
        "first_seen": str(item.get("first_seen") or day.isoformat()),
    }


def _parse_pub(value: object) -> date | None:
    raw = str(value or "")[:10]
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _source_row(article: dict, story_id: str, issue_id: str) -> dict:
    return {
        "hash": article.get("hash", ""),
        "story_id": story_id,
        "issue_id": issue_id,
        "title": article.get("title_kr") or article.get("title", ""),
        "url": article.get("url", ""),
        "publisher": article.get("publisher", ""),
        "topics": article.get("topics") or [],
        "source_kind": "news",
    }


def build(articles: list[dict], today: date, days: int = WINDOW_DAYS,
          story_ids: dict | None = None, issue_ids: dict | None = None,
          official: list[dict] | None = None) -> dict:
    story_ids = story_ids or {}
    issue_ids = issue_ids or {}
    end_window = today + timedelta(days=days)
    events: dict[tuple, dict] = {}
    notes: dict[tuple, dict] = {}
    dropped = 0
    long_range = 0
    official_dropped = 0

    # 공식 일정(official_events.json) — 스토어는 영속이라 창 필터는 여기서.
    official_rows: list[dict] = []
    for item in official or []:
        row = _official_row(item) if isinstance(item, dict) else None
        if row is None:
            continue
        if row["end_date"] < today.isoformat() or row["date"] > end_window.isoformat():
            official_dropped += 1
            continue
        official_rows.append(row)

    for article in articles:
        pub = _parse_pub(article.get("article_date") or article.get("date"))
        if not pub:
            continue
        h = article.get("hash", "")
        story_id = story_ids.get(h, "")
        issue_id = issue_ids.get(h, "")
        # 요약이 첫 재료, 상세(detail)가 보강 — 제목만 있는 기사에는 절이 없다.
        texts = [article.get("summary", ""), article.get("detail", "")]
        seen_sentences = set()
        for text in texts:
            for sentence in _sentences(text):
                if sentence in seen_sentences:
                    continue
                seen_sentences.add(sentence)
                month_only = MONTH_RE.search(sentence)
                title = article.get("title_kr") or article.get("title", "")
                for kind, start, end, label in _extract(sentence, pub, title):
                    # 창 밖 — 이미 끝났거나 너무 멀다. 진행 중 range 는 남긴다.
                    if end < today or start > end_window:
                        dropped += 1
                        continue
                    if kind == "range" and (end - start).days > _RANGE_MAX_DAYS:
                        long_range += 1
                        continue
                    if not label:
                        continue
                    key = (start.isoformat(), end.isoformat(), kind, _norm(label))
                    row = events.get(key)
                    if row is None:
                        events[key] = {
                            "date": start.isoformat(),
                            "end_date": end.isoformat(),
                            "kind": kind,
                            "label": label,
                            "clause": sentence,
                            "origin": "clause",
                            "hash": h,
                            "story_id": story_id,
                            "issue_id": issue_id,
                            "title": article.get("title_kr") or article.get("title", ""),
                            "url": article.get("url", ""),
                            "publisher": article.get("publisher", ""),
                            "topics": article.get("topics") or [],
                            "source_kind": "news",
                            "sources": [_source_row(article, story_id, issue_id)],
                            "first_seen": pub.isoformat(),
                        }
                    else:
                        if not any(s["hash"] == h for s in row["sources"]):
                            row["sources"].append(_source_row(article, story_id, issue_id))
                        if pub.isoformat() < row["first_seen"]:
                            row["first_seen"] = pub.isoformat()
                        if not row["issue_id"] and issue_id:
                            row["issue_id"] = issue_id
                # '9월 중' — 날짜 절이 하나도 없는 문장에서만 달 단위 노트로.
                if month_only and EVENT_KW.search(sentence) and not POINT_RE.search(sentence):
                    year = int(month_only.group("y") or 0)
                    month = int(month_only.group("m"))
                    if not year:
                        year = pub.year + (1 if month < pub.month - 6 else 0)
                    month_key = f"{year:04d}-{month:02d}"
                    first = date(year, month, 1)
                    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
                    if last < today or first > end_window:
                        continue
                    label = _month_label(sentence, article.get("title_kr") or article.get("title"))
                    nkey = (month_key, _norm(label))
                    if nkey not in notes:
                        notes[nkey] = {
                            "month": month_key,
                            "label": label,
                            "url": article.get("url", ""),
                            "publisher": article.get("publisher", ""),
                        }

    # official 을 먼저 넣는다 — _merge_similar 는 먼저 온 행이 병합 타깃이라,
    # 같은 사건을 기사 절도 말할 때 공식 행이 살아남고(공식 배지·시간·장소
    # 유지) 기사 쪽은 sources 로 붙는다.
    rows = _merge_similar(
        official_rows
        + sorted(events.values(), key=lambda r: (r["date"], r["end_date"], r["label"])))
    rows.sort(key=lambda r: (r["date"], r["end_date"], r["label"]))
    if len(rows) > MAX_EVENTS:
        dropped += len(rows) - MAX_EVENTS
        rows = rows[:MAX_EVENTS]
    for row in rows:
        row["source_count"] = len(row["sources"])
        row["id"] = "ev-" + hashlib.sha1(
            f"{row['date']}|{row['end_date']}|{row['label']}".encode("utf-8")).hexdigest()[:12]

    return {
        "start": today.isoformat(),
        "end": end_window.isoformat(),
        "days": days,
        "events": rows,
        "month_notes": sorted(notes.values(), key=lambda n: (n["month"], n["label"])),
        "dropped": {"out_of_window": dropped, "long_range": long_range,
                    "official_out_of_window": official_dropped},
    }
