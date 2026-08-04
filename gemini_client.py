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


class GeminiTruncated(GeminiError):
    """출력 토큰 예산 소진으로 응답이 잘렸다 (finishReason=MAX_TOKENS).

    2.5-flash 는 thinking 토큰이 maxOutputTokens 를 함께 잠식한다. 예산이 바닥나면
    parts 가 통째로 비거나(생각만 하다 끝남) JSON 이 중간에서 잘려 나온다.

    **같은 예산으로 다시 불러도 같은 자리에서 잘린다.** 그래서 이건 재시도 신호가
    아니라 *입력을 줄이라는* 신호다. 호출자가 이 둘을 구분할 수 있도록 따로 뽑았다
    — 429(한도 소진, 재시도 유해)와 섞이면 대응이 정반대가 된다.
    """


def is_available() -> bool:
    """키가 설정되어 있고 호출 가능한지."""
    return bool(API_KEY)


def _salvage_json(text: str) -> dict:
    """깨진 JSON 응답 복구: 코드펜스 제거 → 첫 객체 추출 → 문자열 내 raw 줄바꿈 복구.

    모델이 가끔 펜스/머리말을 붙이거나 문자열 값 안에 줄바꿈을 넣어 'Unterminated
    string'을 만든다. 마지막 시도까지 실패하면 JSONDecodeError 가 그대로 올라가
    call_json 의 재시도 로직으로 처리된다.
    """
    import re
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        s = s[a:b + 1]
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # 문자열 값 안의 raw 줄바꿈을 공백으로 (이스케이프된 \\n 은 건드리지 않음)
        return json.loads(s.replace("\r", " ").replace("\n", " "))


def _finish_reason(payload: object) -> str:
    """candidates[0].finishReason. 구조가 예상과 다르면 빈 문자열."""
    try:
        return payload["candidates"][0].get("finishReason") or ""   # type: ignore[index]
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""


def _truncation_detail(payload: object) -> str:
    """잘림 사유 한 줄 요약.

    payload 전체를 그대로 붙이면 로그에서 앞부분만 남을 때 정작 원인(MAX_TOKENS)이
    잘려 나간다. 사후에 '왜 잘렸나'를 재현 없이 답할 수 있도록 토큰 내역만 짧게 남긴다.
    """
    usage = payload.get("usageMetadata") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        usage = {}
    return (
        "MAX_TOKENS 출력 예산 소진 — "
        f"thoughts={usage.get('thoughtsTokenCount', '?')} "
        f"output={usage.get('candidatesTokenCount', '?')} "
        f"total={usage.get('totalTokenCount', '?')}"
    )


def call_json(
    system_prompt: str,
    user_message: str,
    *,
    temperature: float = 0.1,
    max_output_tokens: int = 4096,
    timeout: float = 60.0,
    retries: int = 3,
    thinking_budget: int | None = None,
) -> dict:
    """system+user 한 쌍을 Gemini에 보내고 JSON 객체로 파싱해 반환.

    - response_mime_type=application/json 으로 펜스·머리말 없는 순수 JSON 강제.
    - 429/일시 오류는 지수 백오프로 retries 만큼 재시도.
    - 파싱 실패 시 GeminiError 발생.
    - thinking_budget=0 은 thinking 을 끈다. 사고가 필요 없는 정형·창작 출력은
      꺼야 한다 — thinking 토큰이 출력 예산을 잠식해 MAX_TOKENS 로 잘린다
      (2026-08-04 실측: 대본 생성이 thoughts=7863/8192 로 output 315에서 잘림.
      로컬은 통과했는데 CI 에서 잘렸다 — thinking 길이는 비결정적이라
      "로컬에서 됐다"가 예산 충분의 근거가 못 된다).
    """
    if not API_KEY:
        raise GeminiError("GEMINI_API_KEY 미설정. .env 또는 GitHub Secrets에 등록 필요.")

    generation_config: dict = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
        "responseMimeType": "application/json",
    }
    if thinking_budget is not None:
        generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

    url = _ENDPOINT.format(model=MODEL) + f"?key={API_KEY}"
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "generationConfig": generation_config,
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
                # parts 가 통째로 없는 가장 흔한 원인은 thinking 이 출력 예산을 다 쓴
                # 것이다. payload 를 그대로 실어 보내면 원인이 로그 뒤로 밀리므로
                # 잘림은 따로 구분해 짧은 사유로 올린다.
                if _finish_reason(payload) == "MAX_TOKENS":
                    raise GeminiTruncated(_truncation_detail(payload)) from e
                raise GeminiError(f"응답 구조 비정상: {payload}") from e
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                try:
                    # 깨진 응답 복구 시도 (펜스·잡텍스트·문자열 내 줄바꿈)
                    return _salvage_json(text)
                except json.JSONDecodeError:
                    # 예산 초과로 잘린 것이면 아래 재시도 절로 흘려보내지 않는다 —
                    # 같은 maxOutputTokens 로 3번 더 불러도 같은 자리에서 잘리고
                    # 무료 티어 한도만 4배로 태운다.
                    if _finish_reason(payload) == "MAX_TOKENS":
                        raise GeminiTruncated(_truncation_detail(payload)) from None
                    raise
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
