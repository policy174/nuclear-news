"""
Gemini API 얇은 wrapper.

connect-ai의 `_quickLLMCall` 패턴을 차용 — 단일 system+user 메시지, JSON 출력 강제,
낮은 temperature, 짧은 timeout. 분류·dedup 같은 결정적 작업 전용.

환경 변수:
    GEMINI_API_KEY   — Google AI Studio 발급 키 (필수)
    GEMINI_MODEL     — 모델 ID (기본 gemini-2.5-flash, 무료 티어 500 RPD)
                       sesang-tracker와 키를 공유하는 환경에서 2.0-flash는
                       free tier limit=0 응답이 나와 2.5로 고정

사용법:
    from gemini_client import call_json
    data = call_json(SYSTEM_PROMPT, user_payload, schema_hint={"groups": [[0,1]]})
    # data == {"groups": [[0,1], [2]]}  같은 형태
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# .env 로딩 (telegram_send.py와 동일 규칙)
_ENV_PATH = Path(__file__).parent / ".env"


def _load_env() -> dict[str, str]:
    if not _ENV_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for raw in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


_ENV_FILE = _load_env()


def _resolve(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key) or _ENV_FILE.get(key) or default


API_KEY = _resolve("GEMINI_API_KEY")
MODEL = _resolve("GEMINI_MODEL", "gemini-2.5-flash")

# Gemini REST 엔드포인트 — SDK 안 쓰고 stdlib urllib만 사용 (의존성 0)
_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


class GeminiError(RuntimeError):
    """Gemini 호출 실패."""


def is_available() -> bool:
    """키가 설정되어 있고 호출 가능한지."""
    return bool(API_KEY)


def call_json(
    system_prompt: str,
    user_message: str,
    *,
    temperature: float = 0.1,
    max_output_tokens: int = 4096,
    timeout: float = 60.0,
    retries: int = 3,
) -> dict:
    """system+user 한 쌍을 Gemini에 보내고 JSON 객체로 파싱해 반환.

    - response_mime_type=application/json 으로 펜스·머리말 없는 순수 JSON 강제.
    - 429/일시 오류는 지수 백오프로 retries 만큼 재시도.
    - 파싱 실패 시 GeminiError 발생.
    """
    if not API_KEY:
        raise GeminiError("GEMINI_API_KEY 미설정. .env 또는 GitHub Secrets에 등록 필요.")

    url = _ENDPOINT.format(model=MODEL) + f"?key={API_KEY}"
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        },
    }

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
            # candidates[0].content.parts[0].text 추출
            try:
                text = payload["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as e:
                raise GeminiError(f"응답 구조 비정상: {payload}") from e
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                # 펜스가 끼어든 경우 한 번 더 정리 시도
                cleaned = text.strip().strip("`").lstrip("json").strip()
                return json.loads(cleaned)
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            last_err = GeminiError(f"HTTP {e.code}: {body_text[:300]}")
            # 429/5xx만 재시도
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise last_err from e
            # 429(무료 티어 분당 한도)는 분당 리셋 → 길게 대기. 5xx는 짧게.
            time.sleep(20 * (attempt + 1) if e.code == 429 else 2 ** attempt)
            continue
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = GeminiError(f"{type(e).__name__}: {e}")
            if attempt == retries:
                raise last_err
            time.sleep(2 ** attempt)
    # 도달 불가
    raise last_err or GeminiError("Gemini 호출 실패")


# 간단한 CLI 자가진단: `python gemini_client.py "ping"`
if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "OK 한 단어만 출력"
    if not is_available():
        print("ERROR: GEMINI_API_KEY 미설정")
        sys.exit(1)
    try:
        out = call_json(
            "당신은 JSON만 출력하는 봇입니다. {\"reply\": \"...\"} 형식.",
            msg,
            max_output_tokens=64,
        )
        print(json.dumps(out, ensure_ascii=False))
    except GeminiError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
