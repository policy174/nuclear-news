"""
이메일 뉴스레터 수집 (ANS Nuclear News Daily 등) — 고품질 seed source.

배경:
    사용자가 구독 중인 ANS 'Nuclear News Daily'(daily@news.ans.org)는 편집진이
    큐레이션한 일일 다이제스트. 메인 기사는 ans.org 링크(이미 RSS로 크롤)지만,
    "In other news" 섹션에 우리 RSS에 없는 외부 매체(Guardian·WyoFile·지역지 등)
    링크가 있음 → 그 외부 링크만 뽑아 기존 수집 파이프라인에 합류시킨다.

설계 원칙 (2026-07 AI 교차검토 반영):
    - 이메일 본문을 LLM에 그대로 던지지 않음. 링크+요약문장만 추출해 기사 후보로.
    - raw email/HTML 은 어디에도 저장 안 함 (상태파일 안 늘림 — git 레이스 방지).
      중복 방지는 기존 article hash(state['sent']) 재사용 → 무상태(stateless).
    - 이미 RSS로 크롤하는 도메인(ans.org·WNN·IAEA)은 스킵 — 순수 신규 소스만.
    - IMAP 미설정/실패 시 조용히 빈 리스트 (수집 파이프라인 영향 0).

필요 GitHub Secrets (미설정이면 이 모듈은 그냥 건너뜀):
    IMAP_USER      — Gmail 주소
    IMAP_PASSWORD  — Gmail 앱 비밀번호 (2단계 인증 → 앱 비밀번호 발급)
    (선택) IMAP_HOST 기본 imap.gmail.com / IMAP_FROM 기본 daily@news.ans.org
"""

from __future__ import annotations

import email
import email.policy
import imaplib
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com")
IMAP_USER = os.environ.get("IMAP_USER", "")
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")
IMAP_FROM = os.environ.get("IMAP_FROM", "daily@news.ans.org")
LOOKBACK_HOURS = 30  # 일간 뉴스레터라 넉넉히 (기사 hash dedup이 중복 방지)

# 이미 RSS로 크롤 중 → 뉴스레터에서 다시 가져오면 중복만 늘어 스킵
_COVERED_DOMAINS = ("ans.org", "world-nuclear-news.org", "iaea.org")
# 기사 아님 (구독관리·SNS·후원 등)
_JUNK_HINTS = ("unsubscribe", "list-manage", "donate", "advertis", "mailto:",
               "twitter.com", "x.com/", "facebook.com", "linkedin.com",
               "youtube.com", "instagram.com", "apple.com", "play.google.com",
               "ans.org/member", "ans.org/join", "surveymonkey", "forms.gle")

_BLOCK_SPLIT_RE = re.compile(r"</(?:p|li|td|div|h[1-6])>", re.IGNORECASE)
_HREF_RE = re.compile(r"<a\s[^>]*href=[\"']([^\"']+)[\"']", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(s: str) -> str:
    import html as _html
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", _html.unescape(s))).strip()


def _unwrap(url: str, timeout: float = 10.0) -> str:
    """트래킹 redirect(informz 등)를 최종 기사 URL로. 실패 시 원본 유지."""
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.geturl()
    except Exception:
        return url


def _extract_candidates(html_body: str) -> list[tuple[str, str]]:
    """HTML → (요약문장, 링크) 후보. 뉴스레터 구조: 문장 끝에 매체명 링크."""
    out: list[tuple[str, str]] = []
    for block in _BLOCK_SPLIT_RE.split(html_body):
        hrefs = _HREF_RE.findall(block)
        if not hrefs:
            continue
        text = _strip_tags(block)
        # 실제 뉴스 요약 문장만 (짧은 네비게이션·버튼 텍스트 배제)
        if len(text) < 60:
            continue
        href = hrefs[-1].strip()  # 블록 마지막 링크 = 출처 매체 관례
        if not href.lower().startswith("http"):
            continue
        if any(j in href.lower() for j in _JUNK_HINTS):
            continue
        out.append((text[:300], href))
    return out


def fetch_newsletter_articles(state_sent: dict) -> list[dict]:
    """뉴스레터 외부 링크 → news_bot article dict 리스트 (같은 형식으로 합류).

    state_sent: news_bot state['sent'] — 기존 hash dedup 재사용.
    """
    if not IMAP_USER or not IMAP_PASSWORD:
        return []  # 미설정 → 조용히 스킵

    # 순환 import 회피 — 호출 시점(news_bot 로딩 완료 후) lazy import
    from news_bot import url_hash, get_domain, is_promotional

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    articles: list[dict] = []

    try:
        imap = imaplib.IMAP4_SSL(IMAP_HOST)
        imap.login(IMAP_USER, IMAP_PASSWORD)
        imap.select("INBOX", readonly=True)  # 읽음 표시 안 건드림 (PEEK)
        since = cutoff.strftime("%d-%b-%Y")
        typ, data = imap.search(None, f'(FROM "{IMAP_FROM}" SINCE "{since}")')
        uids = (data[0] or b"").split()
        print(f"[email] {IMAP_FROM} 최근 메일 {len(uids)}통")

        for uid in uids[-3:]:  # 안전 상한 — 일간지라 최근 3통이면 충분
            typ, msg_data = imap.fetch(uid, "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1], policy=email.policy.default)

            try:
                mail_dt = parsedate_to_datetime(msg["Date"])
                if mail_dt.tzinfo is None:
                    mail_dt = mail_dt.replace(tzinfo=timezone.utc)
            except Exception:
                mail_dt = datetime.now(timezone.utc)
            if mail_dt < cutoff:
                continue

            body = msg.get_body(preferencelist=("html",))
            if body is None:
                continue
            html_body = body.get_content()

            seen_links: set[str] = set()
            for text, href in _extract_candidates(html_body)[:25]:
                final = _unwrap(href)
                dom = get_domain(final)
                if not dom or any(c in dom for c in _COVERED_DOMAINS):
                    continue  # 이미 RSS 커버 or 도메인 불명
                if any(j in final.lower() for j in _JUNK_HINTS):
                    continue
                if final in seen_links:
                    continue
                seen_links.add(final)

                h = url_hash(final)
                if h in state_sent:
                    continue
                title = text[:120]
                if is_promotional(title, text):
                    continue

                articles.append({
                    "hash": h,
                    "title": title,
                    "description": text,
                    "link": final,
                    "pub": mail_dt,
                    "matched": "ANS Nuclear News Daily",
                    "score": 10,  # 편집진 큐레이션 = 신뢰 seed 가중
                    "domain": dom,
                    "feed": "정책",
                })
        imap.logout()
    except Exception as e:  # noqa: BLE001 — 이메일 실패가 수집 전체를 막으면 안 됨
        print(f"[email] 뉴스레터 수집 실패 → 건너뜀: {type(e).__name__}: {str(e)[:120]}")
        return articles

    print(f"[email] 외부 매체 기사 후보 {len(articles)}건")
    return articles
