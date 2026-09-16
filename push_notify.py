# -*- coding: utf-8 -*-
"""웹 푸시 발송 — 아침 브리핑 뒤 구독자 전원에게 "오늘 브리핑" 알림 한 번.

구독 목록은 사이트의 /push/list(토큰), 발송은 pywebpush(VAPID). 죽은 구독(404/410)은
/push/subscribe DELETE 로 지운다. 비치명: 실패해도 브리핑·카드는 이미 나갔다.

환경: PUSH_ADMIN_TOKEN, VAPID_PRIVATE_KEY(PEM), VAPID_SUBJECT(mailto:), SITE_URL(선택)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
BRIEFINGS_FILE = ROOT / "web" / "public" / "data" / "briefings.json"
SITE_URL = (os.environ.get("SITE_URL") or "https://nuclens.pages.dev").rstrip("/")
MAX_TITLES = 3


def build_payload(briefings: list[dict], date: str | None = None) -> dict:
    """알림 본문 = 그날 상위 3건 제목. 사이트가 정한 순서 그대로(카드와 같은 원칙)."""
    days = sorted((b for b in briefings if b.get("issues")), key=lambda b: b["date"])
    if date:
        days = [b for b in days if b["date"] == date] or days
    if not days:
        return {"title": "Nuclens 오늘 브리핑", "body": "오늘의 원전 현안이 올라왔습니다.",
                "url": "/?src=push", "tag": "nuclens-brief"}
    day = days[-1]
    titles = [str(i.get("title") or "").strip() for i in day["issues"][:MAX_TITLES]]
    body = "\n".join(f"{n}. {t[:40]}" for n, t in enumerate(titles, 1) if t)
    return {"title": f"Nuclens {day['date'][5:].replace('-', '/')} 브리핑 · {len(day['issues'])}건",
            "body": body, "url": "/?src=push", "tag": f"nuclens-brief-{day['date']}"}


def main() -> int:
    token = os.environ.get("PUSH_ADMIN_TOKEN")
    private_key = os.environ.get("VAPID_PRIVATE_KEY")
    subject = os.environ.get("VAPID_SUBJECT") or "mailto:nuclens@example.com"
    if not token or not private_key:
        print("[push] PUSH_ADMIN_TOKEN/VAPID_PRIVATE_KEY 미설정 — 스킵")
        return 0
    from pywebpush import WebPushException, webpush  # noqa: PLC0415 — 로컬엔 없을 수 있다
    from py_vapid import Vapid  # noqa: PLC0415

    # pywebpush 는 문자열을 '파일 경로 아니면 base64 raw 키'로만 본다 — 시크릿에 넣은
    # PEM 본문은 어느 쪽도 아니라 'Could not deserialize key data'(2026-09-17 실측).
    if "-----BEGIN" in private_key:
        private_key = Vapid.from_pem(private_key.strip().encode())

    resp = requests.get(f"{SITE_URL}/push/list", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code != 200:
        print(f"[push] 목록 조회 실패 HTTP {resp.status_code}: {resp.text[:120]}")
        return 0
    subs = resp.json().get("subscriptions") or []
    if not subs:
        print("[push] 구독자 0명 — 보낼 곳 없음")
        return 0

    briefings = json.loads(BRIEFINGS_FILE.read_text(encoding="utf-8")) if BRIEFINGS_FILE.exists() else []
    payload = build_payload(briefings, os.environ.get("BRIEF_DATE"))
    data = json.dumps(payload, ensure_ascii=False)

    sent = dead = failed = 0
    for sub in subs:
        try:
            webpush(subscription_info={"endpoint": sub["endpoint"], "keys": sub["keys"]},
                    data=data, vapid_private_key=private_key,
                    vapid_claims={"sub": subject}, ttl=6 * 3600, timeout=15)
            sent += 1
        except WebPushException as exc:
            status = getattr(exc.response, "status_code", None)
            if status in (404, 410):
                dead += 1
                requests.delete(f"{SITE_URL}/push/subscribe", json={"endpoint": sub["endpoint"]}, timeout=15)
            else:
                failed += 1
                print(f"[push] 실패 HTTP {status}: {str(exc)[:100]}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"[push] 실패 {type(exc).__name__}: {str(exc)[:100]}")
    print(f"[push] 발송 {sent} / 만료 정리 {dead} / 실패 {failed} (구독 {len(subs)}) — {payload['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
