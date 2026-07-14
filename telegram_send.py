"""
원자력정책실 동향봇 - 텔레그램 발송 모듈

사용법:
    python telegram_send.py "메시지 내용"
    python telegram_send.py --file 메시지.txt
    python telegram_send.py --file 메시지.md --html

또는 다른 스크립트에서 import:
    from telegram_send import send_text, send_long_text
    send_text("간단한 한 줄")
    send_long_text(long_markdown, mode='html')
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# ---- 설정 로드 ---------------------------------------------------------------
#
# 우선순위:
#   1. 환경변수 (GitHub Actions Secrets, OS env)
#   2. .env 파일 (로컬 개발용, .gitignore 처리됨)

ENV_PATH = Path(__file__).parent / ".env"


def _load_env_file() -> dict[str, str]:
    """.env 파일에서 키-값 쌍 읽기. 파일 없으면 빈 dict 반환."""
    if not ENV_PATH.exists():
        return {}
    config: dict[str, str] = {}
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config


def _resolve(key: str) -> str | None:
    """환경변수 먼저, 없으면 .env 파일에서 가져옴."""
    return os.environ.get(key) or _ENV_FILE.get(key)


_ENV_FILE = _load_env_file()
TOKEN = _resolve("TELEGRAM_BOT_TOKEN")
CHAT_ID = _resolve("TELEGRAM_CHAT_ID")

if not TOKEN or not CHAT_ID:
    sys.exit(
        "ERROR: TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 누락.\n"
        "  - 로컬: .env 파일에 설정\n"
        "  - GitHub Actions: Repository Secrets에 등록"
    )

API = f"https://api.telegram.org/bot{TOKEN}"
MAX_LEN = 4000  # 텔레그램 메시지 한도는 4096; 안전 마진 96자


# ---- 핵심 발송 함수 ----------------------------------------------------------


def send_text(text: str, parse_mode: str | None = "HTML",
              reply_markup: dict | None = None,
              disable_preview: bool = False) -> dict:
    """단일 메시지 발송. 4000자 이내여야 함.

    parse_mode:
        - "HTML"  : <b>굵게</b>, <i>기울임</i>, <a href='url'>링크</a> 지원 (권장)
        - "MarkdownV2" : 텔레그램 마크다운 (이스케이프 까다로움)
        - None    : 평문
    reply_markup:
        - inline keyboard 등 Telegram reply_markup 객체 (dict). 예:
          {"inline_keyboard": [[{"text": "👍", "callback_data": "fb:xxxx:important"}]]}
    """
    data = {"chat_id": CHAT_ID, "text": text}
    if parse_mode:
        data["parse_mode"] = parse_mode
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    if disable_preview:
        data["disable_web_page_preview"] = "true"

    req = urllib.request.Request(
        f"{API}/sendMessage",
        data=urllib.parse.urlencode(data).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API HTTP {e.code}: {body}") from e


def send_long_text(text: str, parse_mode: str | None = "HTML",
                   reply_markup: dict | None = None,
                   disable_preview: bool = False) -> list[dict]:
    """긴 메시지를 자동으로 여러 메시지로 쪼개서 발송.

    가능하면 단락(\\n\\n) 경계에서 끊고, 안 되면 줄(\\n) 경계, 그것도 안 되면
    글자 단위로 잘라서 보냄. reply_markup 은 마지막 청크에만 부착
    (피드백 키보드가 브리핑 끝에 오도록).
    """
    chunks = _split_by_length(text, MAX_LEN)
    results = []
    for i, chunk in enumerate(chunks):
        if i > 0:
            time.sleep(0.5)  # 텔레그램 rate limit 회피
        markup = reply_markup if i == len(chunks) - 1 else None
        results.append(send_text(chunk, parse_mode=parse_mode, reply_markup=markup,
                                 disable_preview=disable_preview))
    return results


def _split_by_length(text: str, limit: int) -> list[str]:
    """텍스트를 limit 길이 이하 청크로 분할. 단락→줄→글자 순으로 끊는다."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        # 단락 경계 우선
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


# ---- CLI 진입점 --------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="텔레그램 봇으로 메시지 보내기")
    parser.add_argument("message", nargs="?", help="보낼 메시지 내용 (직접 입력)")
    parser.add_argument("--file", "-f", help="메시지 파일 경로 (UTF-8)")
    parser.add_argument("--plain", action="store_true", help="평문 발송 (서식 비활성화)")
    parser.add_argument("--markdown", action="store_true", help="MarkdownV2 모드")
    args = parser.parse_args()

    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    elif args.message:
        text = args.message
    else:
        parser.error("메시지 또는 --file 중 하나는 필수")

    if args.plain:
        mode = None
    elif args.markdown:
        mode = "MarkdownV2"
    else:
        mode = "HTML"

    results = send_long_text(text, parse_mode=mode)
    ok_count = sum(1 for r in results if r.get("ok"))
    print(f"[OK] {ok_count}/{len(results)} 메시지 발송 완료")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
