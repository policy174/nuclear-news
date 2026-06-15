"""
원자력 동향 리서치 → 텔레그램 자동 발송 (다중 키워드)

사용법:
    python send_research.py                        # keywords.json의 모든 주간 토픽 실행
    python send_research.py --topic "SMR 동향"      # 특정 토픽 1개만 실행
    python send_research.py --include-biweekly     # 격주 토픽도 포함
    python send_research.py --dry-run              # 텔레그램 발송 없이 실행

작동 흐름:
    1. keywords.json 읽기
    2. 각 토픽마다 last30days 엔진 실행 (subqueries로 구성된 plan 사용)
    3. 결과 마크다운 파싱 → 품질 필터 적용
    4. 토픽별로 텔레그램 메시지 발송
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import date
from html import escape
from pathlib import Path

# Windows 콘솔 UTF-8 강제 (이모지 출력 시 cp949 인코딩 에러 방지)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from telegram_send import send_long_text
from dedup import dedup_clusters
from scorer import score_clusters
from sources import credibility_bonus
from synthesize import build_cards, format_cards_message

# ---- 환경 설정 (Windows / Linux 양쪽 호환) -----------------------------------

ROOT = Path(__file__).parent

# Python 경로
PYTHON = os.environ.get("PYTHON_BIN") or sys.executable

# last30days 스킬 경로 (env > Linux GitHub Actions > Windows 로컬 순)
SKILL_ROOT = Path(
    os.environ.get("LAST30DAYS_SKILL_ROOT")
    or (
        Path.home() / "last30days-skill" / "skills" / "last30days"
        if os.name != "nt"
        else Path.home() / ".claude" / "skills" / "last30days"
    )
)
SCRIPT = SKILL_ROOT / "scripts" / "last30days.py"

# 결과 저장 경로
SAVE_DIR = Path(
    os.environ.get("LAST30DAYS_SAVE_DIR")
    or (
        ROOT / "raw"
        if os.name != "nt"
        else Path.home() / "Documents" / "Last30Days"
    )
)
SAVE_DIR.mkdir(parents=True, exist_ok=True)

# Windows winget yt-dlp/ffmpeg PATH 보강 (로컬 실행 시)
if os.name == "nt":
    pkg_root = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    extra_paths = []
    for p in pkg_root.glob("yt-dlp.yt-dlp_*"):
        extra_paths.append(str(p))
    for p in pkg_root.glob("yt-dlp.FFmpeg_*"):
        for bin_dir in p.glob("ffmpeg-*/bin"):
            extra_paths.append(str(bin_dir))
            break
    if extra_paths:
        os.environ["PATH"] = os.pathsep.join(extra_paths) + os.pathsep + os.environ.get("PATH", "")


# ---- 품질 필터 설정 ----------------------------------------------------------

# 정책·산업 신뢰 X 핸들 (이들의 글은 가산점, @ 시작이어도 통과)
# 양현진님이 검토 후 수정/추가하시면 됩니다.
CREDIBLE_HANDLES = {
    # 국제기구·정부
    "IAEAorg", "World_Nuclear", "OECD_NEA", "NEI", "ANS_org",
    "energydotgov", "ENERGY", "NRCgov", "IEA", "DOEnuclear",
    # 언론·전문 매체
    "WNN_Updates", "NucNet", "PowerMag", "PoliticoEU",
    "axios", "reuters", "BloombergNRG", "FT_Energy",
    # 분석가·평론가 (검증된)
    "Nuclear_BP", "MarkNelson", "decoupling", "DrSimEvans",
    "EnergyPolicies", "JackDevanney", "OllyKitan",
    # 학자·연구자
    "Roger_J_Pielke", "Stewart_Brand", "noahpinion",
    # 기업
    "NuScale_Power", "TerraPower", "X_energy", "Oklo",
    "RollsRoyceSMR", "Westinghouse", "Constellation",
}

# 정책 관련성 높은 서브레딧 (보너스 점수)
POLICY_SUBS = {
    "nuclear", "NuclearPower", "energy", "climate",
    "geopolitics", "IRstudies", "EnergyPolicy", "energytransition",
}

# 노이즈 키워드 (해당 단어 포함 시 제외)
NOISE_PROFANITY = ["fuck", "shit", "retard", "damn it"]
NOISE_HOBBIES = ["roblox", "minecraft", "marble run", "factorio"]
NOISE_SELFMADE_PREFIXES = (
    "so i made", "i made a", "i built", "look at my", "made it in",
    "check out my", "my first", "is this a good",
)

# X 참여도 하한선 (무명 핸들 차단용 - "팔로워 N명 이상 유명인" 의 등가물)
MIN_X_LIKES = 5      # 좋아요 5개 이상 OR
MIN_X_RETWEETS = 2   # 리트윗 2개 이상

# 단일 댓글·저참여 차단을 위한 최소 점수
MIN_SCORE_THRESHOLD = 3


# ---- 1. 검색 실행 ------------------------------------------------------------


def build_plan(subqueries: list[str]) -> str:
    """토픽의 subqueries 리스트로 last30days --plan JSON 생성. HN 제외."""
    sq_objs = []
    for i, sq in enumerate(subqueries):
        sq_objs.append({
            "label": f"sq{i+1}",
            "search_query": sq,
            "ranking_query": f"What major news about {sq} happened recently?",
            # HN 제외, Polymarket 포함
            "sources": ["reddit", "x", "youtube", "polymarket"],
            "weight": 1.0 if i == 0 else 0.7,
        })
    plan = {
        "intent": "news",
        "freshness_mode": "strict_recent",
        "cluster_mode": "story",
        "subqueries": sq_objs,
    }
    return json.dumps(plan, ensure_ascii=False)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def run_research(topic_label: str, subqueries: list[str], subreddits: str) -> Path:
    """last30days 실행 후 저장된 raw 파일 경로 반환."""
    plan = build_plan(subqueries)
    primary_topic = subqueries[0]
    suffix = f"auto-{date.today().isoformat()}"
    expected = SAVE_DIR / f"{slugify(primary_topic)}-raw-{suffix}.md"

    cmd = [
        str(PYTHON),
        str(SCRIPT),
        primary_topic,
        "--emit=compact",
        f"--subreddits={subreddits}",
        "--plan", plan,
        f"--save-dir={SAVE_DIR}",
        f"--save-suffix={suffix}",
    ]

    print(f"[검색] '{topic_label}' (primary: {primary_topic})")
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=420,
    )
    print(f"[검색] 종료 코드 {result.returncode}")

    if expected.exists():
        return expected

    # 저장 경로 stdout에서 추출 시도
    m = re.search(r"Saved output to (.+\.md)", result.stdout)
    if m:
        return Path(m.group(1).strip().replace("~", str(Path.home())))

    raise FileNotFoundError(
        f"raw 파일을 찾을 수 없음 (예상: {expected})\nstderr 일부:\n{result.stderr[:500]}"
    )


# ---- 2. 파싱 -----------------------------------------------------------------

CLUSTER_HEAD_RE = re.compile(
    r"^###\s+\d+\.\s+(?P<title>.+?)\s+\(score\s+(?P<score>\d+),\s+\d+\s+items?,\s+sources?:\s+(?P<sources>[^)]+)\)\s*$",
    re.MULTILINE,
)
URL_RE = re.compile(r"^\s*-\s+URL:\s+(\S+)", re.MULTILINE)
META_RE = re.compile(r"^\s*-\s+\d{4}-\d{2}-\d{2}\s+\|\s+(?P<meta>.+?)$", re.MULTILINE)
# Evidence: 소셜 글의 실제 본문 텍스트 — 카드 합성의 grounding 근거로 사용
EVIDENCE_RE = re.compile(r"^\s*-\s+Evidence:\s+(?P<ev>.+?)\s*$", re.MULTILINE)
STATS_LINE_RE = re.compile(
    r"^-\s+(?P<source>Reddit|X|Youtube|Hacker News|Polymarket):\s+(?P<count>\d+)\s+items?",
    re.MULTILINE,
)

# X 참여도 메타 파싱: [Nlikes, Mrt, Lre] 등
X_LIKES_RE = re.compile(r"\[\s*(?:.*?,\s*)?(\d+)\s*likes?", re.IGNORECASE)
X_RT_RE = re.compile(r"(\d+)\s*rt", re.IGNORECASE)


def parse_clusters(md_text: str, limit: int = 999) -> list[dict]:
    clusters: list[dict] = []
    matches = list(CLUSTER_HEAD_RE.finditer(md_text))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        body = md_text[start:end]

        url_match = URL_RE.search(body)
        meta_match = META_RE.search(body)
        # 클러스터 내 모든 Evidence 텍스트를 모아 본문(grounding)으로 사용
        evidences = [e.strip() for e in EVIDENCE_RE.findall(body) if e.strip()]
        fulltext = " ".join(evidences)[:3000]

        clusters.append({
            "title": m.group("title").strip(),
            "score": int(m.group("score")),
            "sources": [s.strip() for s in m.group("sources").split(",")],
            "url": url_match.group(1) if url_match else None,
            "meta": meta_match.group("meta").strip() if meta_match else "",
            "fulltext": fulltext,
        })
    clusters.sort(key=lambda c: c["score"], reverse=True)
    return clusters[:limit]


def parse_stats(md_text: str) -> dict[str, int]:
    return {m.group("source"): int(m.group("count")) for m in STATS_LINE_RE.finditer(md_text)}


# ---- 2-1. 품질 필터 ----------------------------------------------------------


def parse_x_engagement(meta: str) -> tuple[int, int]:
    """X 메타 라인에서 (likes, retweets) 추출. 못 찾으면 (0, 0)."""
    likes_m = X_LIKES_RE.search(meta)
    rt_m = X_RT_RE.search(meta)
    likes = int(likes_m.group(1)) if likes_m else 0
    rts = int(rt_m.group(1)) if rt_m else 0
    return likes, rts


def is_credible_x_handle(meta: str) -> bool:
    """메타에 신뢰 핸들이 포함됐는지."""
    meta_lower = meta.lower()
    return any(f"@{h.lower()}" in meta_lower for h in CREDIBLE_HANDLES)


def is_noise(cluster: dict) -> str | None:
    """노이즈 사유 반환. None이면 통과."""
    title = cluster["title"]
    title_lower = title.lower()
    meta = cluster.get("meta", "")
    meta_lower = meta.lower()
    sources = cluster.get("sources", [])

    # 1. X 답글 체인 (@로 시작) — 신뢰 핸들이 작성한 글이면 통과
    if title.startswith("@") and not is_credible_x_handle(meta):
        return "x-reply-chain"

    # 2. 욕설
    for w in NOISE_PROFANITY:
        if re.search(rf"\b{re.escape(w)}\b", title_lower):
            return f"profanity:{w}"

    # 3. 게임/취미 콘텐츠
    for kw in NOISE_HOBBIES:
        if kw in title_lower:
            return f"hobby:{kw}"

    # 4. 자작 콘텐츠
    for prefix in NOISE_SELFMADE_PREFIXES:
        if title_lower.startswith(prefix):
            return f"self-made:{prefix}"

    # 5. X 무명 핸들 + 저참여도 ("팔로워 적은 무명인" 차단)
    is_x_only = sources == ["X"]
    if is_x_only and not is_credible_x_handle(meta):
        likes, rts = parse_x_engagement(meta)
        if likes < MIN_X_LIKES and rts < MIN_X_RETWEETS:
            return f"x-low-engagement(likes={likes},rt={rts})"

    # 6. 너무 낮은 점수
    if cluster.get("score", 0) < MIN_SCORE_THRESHOLD:
        return f"low-score:{cluster.get('score', 0)}"

    return None


def quality_boost(cluster: dict) -> int:
    bonus = 0
    meta_lower = cluster.get("meta", "").lower()

    if is_credible_x_handle(cluster.get("meta", "")):
        bonus += 25

    for sub in POLICY_SUBS:
        if f"r/{sub.lower()}" in meta_lower:
            bonus += 15
            break

    # 공신력 출처 보너스 (sources.json: WNN·NucNet·IAEA 등 tier1 +40, tier2 +20)
    bonus += credibility_bonus(cluster)

    return bonus


def filter_and_rank(clusters: list[dict], limit: int) -> tuple[list[dict], list[tuple[str, str]]]:
    rejected: list[tuple[str, str]] = []
    kept: list[dict] = []
    for c in clusters:
        reason = is_noise(c)
        if reason:
            rejected.append((c["title"][:80], reason))
            continue
        c["boosted_score"] = c["score"] + quality_boost(c)
        kept.append(c)
    kept.sort(key=lambda c: c["boosted_score"], reverse=True)
    return kept[:limit], rejected


# ---- 3. 텔레그램 메시지 포맷 -------------------------------------------------


def format_message(topic_label: str, primary_topic: str, clusters: list[dict],
                    stats: dict[str, int], raw_path: Path) -> str:
    today = date.today().isoformat()
    emoji = {"Reddit": "🟠", "X": "🔵", "Youtube": "🔴",
             "Hacker News": "🟡", "Polymarket": "🟣"}

    stats_parts = []
    for src, n in stats.items():
        if n > 0:
            stats_parts.append(f"{emoji.get(src, '·')} {src} {n}")
    stats_line = " · ".join(stats_parts) if stats_parts else "검색 결과 없음"

    lines = [
        f"<b>📰 {escape(topic_label)} ({today})</b>",
        f"<b>검색어:</b> <code>{escape(primary_topic)}</code>",
        f"<b>수집:</b> {stats_line}",
        "",
        "<b>🔥 TOP 헤드라인</b>",
        "",
    ]

    if not clusters:
        lines.append("<i>이번 주는 의미 있는 헤드라인이 잡히지 않았습니다.</i>")
        lines.append("")
    else:
        for i, c in enumerate(clusters, 1):
            title = escape(c["title"][:200])
            meta = escape(c["meta"][:120])
            sources = ", ".join(c["sources"])
            url = c["url"]

            if url:
                lines.append(f"{i}. <a href=\"{escape(url, quote=True)}\">{title}</a>")
            else:
                lines.append(f"{i}. {title}")
            if meta:
                lines.append(f"   <i>{meta}</i>")
            score_meta = f"[{sources}] · score {c.get('boosted_score', c['score'])}"
            if c.get("ai_score") is not None:
                score_meta += f" · AI {c['ai_score']}"
            lines.append(f"   <code>{score_meta}</code>")
            lines.append("")

    return "\n".join(lines)


# ---- 4. 다중 토픽 오케스트레이션 ---------------------------------------------


def load_keywords() -> list[dict]:
    # 소셜 토픽은 social_topics.json (keywords.json 은 news_bot RSS 설정이라 분리)
    path = ROOT / "social_topics.json"
    return json.loads(path.read_text(encoding="utf-8"))["topics"]


def filter_topics_by_schedule(topics: list[dict], include_biweekly: bool) -> list[dict]:
    """현재 주차에 해당하는 토픽만 선별."""
    if not include_biweekly:
        return [t for t in topics if t.get("schedule") == "weekly"]

    # 격주 판단: ISO week number의 홀짝
    week = date.today().isocalendar()[1]
    is_odd_week = (week % 2 == 1)

    selected = []
    for t in topics:
        sched = t.get("schedule", "weekly")
        if sched == "weekly":
            selected.append(t)
        elif sched == "biweekly_odd" and is_odd_week:
            selected.append(t)
        elif sched == "biweekly_even" and not is_odd_week:
            selected.append(t)
    return selected


def collect_topic(topic: dict, top_n: int) -> dict:
    """토픽 1개의 검색·파싱·필터까지만 수행. 발송은 안 함.

    Returns dict with: label, ok, clusters, stats, raw_path, primary, error?
    """
    label = topic["label"]
    subqueries = topic["subqueries"]
    subreddits = topic.get("subreddits", "nuclear,NuclearPower,energy")

    try:
        raw_path = run_research(label, subqueries, subreddits)
    except Exception as e:
        print(f"[에러] '{label}' 검색 실패: {e}")
        return {"label": label, "ok": False, "error": str(e),
                "clusters": [], "stats": {}, "raw_path": None, "primary": subqueries[0]}

    md = raw_path.read_text(encoding="utf-8")
    all_clusters = parse_clusters(md, limit=999)
    # top_n의 ~2배까지 유지 — dedup으로 일부 빠질 것을 대비
    clusters, rejected = filter_and_rank(all_clusters, limit=top_n * 2)
    stats = parse_stats(md)

    print(f"[필터] {label}: 전체 {len(all_clusters)} → 노이즈 {len(rejected)} → 상위 {len(clusters)}")

    return {
        "label": label, "ok": True,
        "clusters": clusters, "stats": stats,
        "raw_path": raw_path, "primary": subqueries[0],
    }


def send_topic_message(label: str, primary: str, clusters: list[dict],
                       stats: dict[str, int], raw_path,
                       top_n: int, dry_run: bool,
                       cards: list[dict] | None = None) -> dict:
    """수집·dedup 끝난 cluster를 받아 메시지 포맷하고 발송.

    cards 가 있으면 합성 카드 형식으로, 없으면 기존 헤드라인 리스트로 폴백.
    """
    if cards:
        cards = cards[:top_n]
        message = format_cards_message(cards, header=label)
        item_count = len(cards)
    else:
        clusters = clusters[:top_n]
        message = format_message(label, primary, clusters, stats, raw_path)
        item_count = len(clusters)

    if dry_run:
        print(f"[dry-run] '{label}' 발송 생략 ({len(message)}자, 항목 {item_count}개)")
        return {"label": label, "ok": True, "dry_run": True, "items": item_count}

    try:
        results = send_long_text(message, parse_mode="HTML")
        ok_count = sum(1 for r in results if r.get("ok"))
        print(f"[발송] '{label}': {ok_count}/{len(results)} 성공 (항목 {item_count}개)")
        return {"label": label, "ok": ok_count == len(results), "items": item_count}
    except Exception as e:
        print(f"[에러] '{label}' 발송 실패: {e}")
        return {"label": label, "ok": False, "error": str(e)}


# ---- 5. CLI ------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="원자력 동향 다중 키워드 자동 발송")
    parser.add_argument("--topic", help="단일 토픽 라벨 (생략 시 keywords.json의 모든 weekly 토픽)")
    parser.add_argument("--include-biweekly", action="store_true",
                        help="격주 토픽도 포함 (홀수주/짝수주 자동 판단)")
    parser.add_argument("--top", type=int, default=8, help="토픽당 상위 헤드라인 개수 (기본 8)")
    parser.add_argument("--score-threshold", type=float, default=6.0,
                        help="AI 점수 임계값. 이 점수 미만 헤드라인은 발송 전 컷 (기본 6.0). "
                             "GEMINI_API_KEY 없으면 미적용.")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 발송 없이 실행")
    args = parser.parse_args()

    topics = load_keywords()
    if args.topic:
        topics = [t for t in topics if t["label"] == args.topic]
        if not topics:
            print(f"ERROR: 토픽 '{args.topic}' 을 keywords.json에서 찾을 수 없음")
            return 1
    else:
        topics = filter_topics_by_schedule(topics, include_biweekly=args.include_biweekly)

    print(f"=== 처리 대상 토픽 {len(topics)}개 ===")
    for t in topics:
        print(f"  - {t['label']} (subqueries: {len(t['subqueries'])}개)")
    print()

    # ---- Phase 1: 모든 토픽 수집·필터 -----------------------------------
    collected: list[dict] = []
    for topic in topics:
        collected.append(collect_topic(topic, top_n=args.top))

    # ---- Phase 2a: AI 점수 매기기 + threshold 필터 ----------------------
    # dedup 앞에 끼움 — 노이즈 먼저 컷해야 의미 dedup 호출 토큰·비용 절감.
    print("\n=== AI 점수 매기기 시작 ===")
    pairs: list[tuple[str, dict]] = []
    for r in collected:
        if not r.get("ok"):
            continue
        for c in r["clusters"]:
            pairs.append((r["label"], c))
    print(f"[score] 전체 cluster {len(pairs)}개 입력 (threshold={args.score_threshold})")

    if pairs:
        pairs, score_dropped = score_clusters(pairs, threshold=args.score_threshold)
        print(f"[score] kept {len(pairs)}, dropped {len(score_dropped)}")
        for t_lbl, c, ai_s, ai_r in score_dropped[:15]:
            print(f"  · drop [{t_lbl}] '{c['title'][:55]}' ({ai_s}점: {ai_r[:40]})")
        if len(score_dropped) > 15:
            print(f"  · ... and {len(score_dropped) - 15} more")

    # ---- Phase 2b: cross-topic dedup -------------------------------------
    print("\n=== cross-topic dedup 시작 ===")
    print(f"[dedup] {len(pairs)}개 cluster 입력")

    if pairs:
        kept_pairs, dropped = dedup_clusters(pairs)
        print(f"[dedup] kept {len(kept_pairs)}, dropped {len(dropped)}")
        for t_lbl, c, kept_t, why in dropped[:20]:  # 최대 20건만 로그
            print(f"  · drop [{t_lbl}] '{c['title'][:60]}' → kept in [{kept_t}] ({why})")
        if len(dropped) > 20:
            print(f"  · ... and {len(dropped) - 20} more")

        # 토픽별 dict로 재그룹핑
        kept_by_topic: dict[str, list[dict]] = {}
        for t_lbl, c in kept_pairs:
            kept_by_topic.setdefault(t_lbl, []).append(c)
        # boosted_score 내림차순
        for lst in kept_by_topic.values():
            lst.sort(key=lambda c: c.get("boosted_score", c.get("score", 0)), reverse=True)
    else:
        kept_by_topic = {}

    # ---- Phase 2c: 카드 합성 (1회 호출로 전체) --------------------------
    # top_n 까지만 카드화 — 토큰 절감. 실패/키없음 시 cards=None → 리스트 폴백.
    print("\n=== 카드 합성 시작 ===")
    synth_pairs: list[tuple[str, dict]] = []
    for lbl, lst in kept_by_topic.items():
        for c in lst[:args.top]:
            synth_pairs.append((lbl, c))

    cards_by_topic: dict[str, list[dict]] = {}
    if synth_pairs:
        cards = build_cards(synth_pairs)
        if cards is None:
            print("[카드] 합성 불가(키 없음/실패) → 기존 리스트 형식으로 폴백")
        else:
            for card in cards:
                cards_by_topic.setdefault(card["topic"], []).append(card)
            print(f"[카드] {len(cards)}장 생성, {len(cards_by_topic)}개 토픽")

    # ---- Phase 3: 토픽별 발송 -------------------------------------------
    print("\n=== 발송 시작 ===")
    summary: list[dict] = []
    for i, r in enumerate(collected):
        if i > 0:
            time.sleep(2)  # 텔레그램 rate limit
        if not r.get("ok"):
            summary.append({"label": r["label"], "ok": False, "error": r.get("error", "수집 실패")})
            continue
        clusters_for_topic = kept_by_topic.get(r["label"], [])
        summary.append(send_topic_message(
            label=r["label"], primary=r["primary"],
            clusters=clusters_for_topic, stats=r["stats"],
            raw_path=r["raw_path"], top_n=args.top, dry_run=args.dry_run,
            cards=cards_by_topic.get(r["label"]),
        ))

    print("\n=== 최종 요약 ===")
    for s in summary:
        status = "✅" if s["ok"] else "❌"
        items = s.get("items", 0)
        err = f" - {s.get('error', '')}" if not s["ok"] else f" - 헤드라인 {items}개"
        print(f"  {status} {s['label']}{err}")

    failures = [s for s in summary if not s["ok"]]
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
