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
import re
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

# 발송 경로가 쿼터에 굶었을 때 물러설 모델. 무료 티어 한도는 **모델별 버킷**이라
# (429 본문의 quotaId 가 `...PerModel-FreeTier`) 상시 파이프라인이 flash 를 다
# 태운 날에도 다른 모델은 남아 있다. 실측 2026-08-22: flash 가 온종일 429 인
# 동안 flash-lite 는 같은 키로 9회 성공했다.
FALLBACK_MODEL = _resolve("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite")

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


# 429 응답은 **얼마나 기다려야 하는지를 서버가 알려준다.** 두 군데에 들어 있다:
#   details[] 의 google.rpc.RetryInfo.retryDelay ("42s")
#   message 끝의 "Please retry in 42.364493284s."
# 이걸 무시하고 고정 사다리(20/40/60초)를 쓰면 **첫 두 번의 재시도는 실패가
# 보장돼 있고 각각 요청을 하나씩 더 태운다** — 분당 한도를 맞고 있는 와중에.
# 실측 2026-08-06 크롤: limit 20/min 인데 서버가 42초를 요구했고 사다리는 20초에
# 첫 재시도를 냈다.
_RETRY_DELAY_RE = re.compile(r"retry in\s+([0-9]+(?:\.[0-9]+)?)\s*s", re.IGNORECASE)
_RETRY_DELAY_FIELD_RE = re.compile(r'"retryDelay"\s*:\s*"([0-9]+(?:\.[0-9]+)?)s"')

# 서버가 말도 안 되게 긴 값을 주면(또는 파싱이 어긋나면) 잡을 통째로 잡아먹는다.
# 한 프로세스가 429 대기로 쓸 수 있는 총 시간. 넘으면 그 뒤의 429 는 자지 않고
# 즉시 포기한다.
#
# 2026-08-21 실사고: 크롤이 429 대기로만 ~12분을 자다가 news_bot 이 25분을 쓰고,
# 작업 상한 30분에서 **Deploy 스텝이 통째로 잘렸다**(2회 연속). 키가 말라 있을 때의
# 재시도는 실패가 보장된 요청이고, 그 대가로 배포가 죽는다 — 늦은 사이트보다
# 갱신 안 되는 사이트가 나쁘다. 한도가 분당인지 일일인지 본문으로 확실히 못
# 가르므로(위 _is_daily_quota 주석) 판정 대신 **총량**으로 막는다.
RETRY_SLEEP_BUDGET_SEC = float(os.environ.get("GEMINI_RETRY_SLEEP_BUDGET", "300"))
_slept_total = 0.0


def retry_sleep_spent() -> float:
    """이 프로세스가 429 대기로 쓴 총 시간(초)."""
    return _slept_total


def reset_retry_budget() -> None:
    global _slept_total
    _slept_total = 0.0


# 이 모듈은 한글·em대시를 찍는다. Windows 기본 코덱(cp949)에서는 그 print 가
# UnicodeEncodeError 로 죽는데, news_bot 처럼 stdout 을 먼저 재설정하는 모듈이
# 함께 로드된 경우에만 우연히 살아남았다 — 단독 실행·단위 테스트에서 터진다.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


RETRY_DELAY_MAX = 75.0
RETRY_DELAY_BUFFER = 1.0   # 값이 정확해서 딱 맞춰 자면 경계에서 또 튕긴다


def _raw_retry_delay(body_text: str) -> float | None:
    """429 본문의 대기 시간 원값(초). 판정에는 클램프 전 값이 필요하다."""
    for pattern in (_RETRY_DELAY_FIELD_RE, _RETRY_DELAY_RE):
        found = pattern.search(body_text or "")
        if found:
            try:
                seconds = float(found.group(1))
            except ValueError:
                continue
            if seconds > 0:
                return seconds
    return None


def _retry_delay_seconds(body_text: str) -> float | None:
    """429 본문이 알려주는 대기 시간(초). 못 찾으면 None — 호출자가 사다리를 쓴다."""
    seconds = _raw_retry_delay(body_text)
    if seconds is None:
        return None
    return min(seconds + RETRY_DELAY_BUFFER, RETRY_DELAY_MAX)


# 429 는 두 종류다. 분당 한도(RPM)는 기다리면 풀리지만 일일 한도(RPD)는 오늘 안
# 풀리지 않는다. 응답 details 의 QuotaFailure.violations[].quotaId 에 한도 종류가
# 들어 있다(예: GenerateRequestsPerDayPerProjectPerModel).
# ── 호출 계측 ──────────────────────────────────────────────────────────────
#
# 왜 필요한가: 2026-08-06 크롤이 429 를 6번 맞았는데 **전부 분당 20회(RPM)** 였다
# (`limit: 20, model: gemini-2.5-flash`, PerDay 표지 0건). 그런데 그 1분에 누가
# 몇 번 불렀는지가 로그에 없어서 원인을 두 번 잘못 짚었다 —
#   ① "일일 한도 소진" (틀림, RPM 이었다)
#   ② "격리 항목 개별 재시도" (틀림, 품질 게이트 재생성은 배치 1회다)
# 세 번째 추측 대신 센다. 처방이 갈리기 때문이다: 재시도가 범인이면 재시도를
# 줄이고, 동시 호출자가 범인이면 중앙 예산이 필요하다.
#
# 호출 지점이 call_json 하나뿐이라 여기만 잡으면 모든 호출자가 세진다.
_CALL_LOG: list[tuple[float, str, str]] = []   # (시각, 모델, 라벨)
CALL_LOG_LIMIT = 5000   # 폭주해도 메모리를 먹지 않게 — 넘으면 그냥 안 쌓는다


def _record_call(model: str, label: str) -> None:
    if len(_CALL_LOG) < CALL_LOG_LIMIT:
        _CALL_LOG.append((time.monotonic(), model, label))


def reset_call_log() -> None:
    _CALL_LOG.clear()


def call_stats() -> dict:
    """모델별 호출 수, 라벨별 내역, **최대 분당 호출 수**.

    최대 분당이 핵심이다. 총 호출이 27회여도 60초 창에 21회가 몰렸으면 RPM 20 을
    넘는다. 슬라이딩 윈도로 재야 '시:분' 경계로 나누는 것과 달리 실제 한도와
    같은 방식이 된다.
    """
    per_model: dict[str, int] = {}
    per_label: dict[str, int] = {}
    for _stamp, model, label in _CALL_LOG:
        per_model[model] = per_model.get(model, 0) + 1
        per_label[label] = per_label.get(label, 0) + 1

    peak: dict[str, int] = {}
    for model in per_model:
        stamps = [s for s, m, _ in _CALL_LOG if m == model]
        best = 0
        for i, start in enumerate(stamps):
            count = sum(1 for s in stamps[i:] if s - start < 60.0)
            best = max(best, count)
        peak[model] = best
    return {"total": len(_CALL_LOG), "per_model": per_model,
            "per_label": per_label, "peak_per_minute": peak}


def format_call_stats() -> str:
    stats = call_stats()
    if not stats["total"]:
        return "[gemini] 호출 0회"
    parts = []
    for model, count in sorted(stats["per_model"].items(), key=lambda kv: -kv[1]):
        parts.append(f"{model} {count}회(최대 분당 {stats['peak_per_minute'][model]})")
    breakdown = " / ".join(f"{label} {count}"
                           for label, count in sorted(stats["per_label"].items(),
                                                      key=lambda kv: -kv[1]))
    return f"[gemini] 호출 {stats['total']}회 · " + " · ".join(parts) + f" · [{breakdown}]"


_DAILY_QUOTA_MARKERS = ("PerDay", "per_day", "PerDayPerProject", "RequestsPerDay")
# 이보다 짧은 재시도 지연은 분당 한도의 신호다 — 일일 한도면 리셋까지 몇 시간이다.
DAILY_QUOTA_MIN_DELAY_SEC = 300.0

# ── 전역 페이서 ────────────────────────────────────────────────────────────
# 무료 티어 한도는 **API 키 단위**다. 그런데 이 파일을 쓰는 모든 경로(크롤 큐레이션,
# issue_review, trend_insights, daily_lead, 오디오 대본)가 한 키를 공유하면서
# 서로 간격을 모른 채 두드렸다. 2026-08-17 실측: 한 잡 안에서 세 작업이 각자
# 3회씩 재시도하며 "59초 후 재시도"를 계속 받았다 — 재시도가 분당 창을 다시
# 채우는 악순환이라 기다릴수록 나빠졌다. 같은 시각 로컬 단독 호출은 200이었다.
#
# 호출 간 최소 간격을 프로세스 전역에서 강제한다. 실패가 보장된 요청을 아예
# 내보내지 않는 것이 재시도 사다리보다 싸다.
GEMINI_MIN_INTERVAL_SEC = float(_resolve("GEMINI_MIN_INTERVAL_SEC", "4.0"))
_last_call_at = 0.0


def _pace() -> None:
    """직전 호출로부터 최소 간격이 지나도록 잠근다. 단일 스레드 전제."""
    global _last_call_at
    if GEMINI_MIN_INTERVAL_SEC <= 0:
        return
    wait = _last_call_at + GEMINI_MIN_INTERVAL_SEC - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_call_at = time.monotonic()


def _is_daily_quota(body_text: str) -> bool:
    """429 본문이 일일 한도 소진을 가리키는가.

    판정 불가면 False — 분당 한도로 보고 재시도한다. 재시도 가능한 것을 못 하는
    쪽이, 오늘 안 풀릴 것을 붙잡고 늘어지는 쪽보다 낫다.

    **서버가 짧은 재시도 지연을 주면 마커보다 그쪽을 믿는다.** 하루가 남았는데
    "45초 뒤 재시도"라고 할 리 없다. 2026-08-17 실사고: limit 20 / retry in
    12~45s 응답을 PerDay 로 읽고 오디오가 하루를 통째로 포기했는데, 같은 키로
    같은 모델을 직접 부르니 200 이었다. 마커 문자열은 본문 어디에 있어도
    걸리지만(다른 violation·도움말 링크 포함), retryDelay 는 서버가 이 요청에
    대해 내린 판단이다.
    """
    delay = _raw_retry_delay(body_text)
    if delay is not None and delay <= DAILY_QUOTA_MIN_DELAY_SEC:
        return False
    return any(marker in body_text for marker in _DAILY_QUOTA_MARKERS)


def _daily_quota_marker(body_text: str) -> str:
    """판정을 만든 표지. 로그용 — 어느 문자열이 걸렸는지가 오진의 유일한 증거다."""
    return next((m for m in _DAILY_QUOTA_MARKERS if m in body_text), "없음")


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
    model: str | None = None,
    fallback_model: str | None = None,
    label: str = "unlabeled",
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
    - model 을 주면 기본 MODEL 대신 그 모델을 부른다. 무료 티어 쿼터는 모델별
      버킷이다 — 하루 1회짜리 호출을 상시 파이프라인(크롤 큐레이션)과 같은
      버킷에 두면 저녁마다 굶는다 (2026-08-04 실측: 같은 시각 프로브는
      성공하는데 브리핑 체인 끝의 호출만 3연속 429).
    """
    if not API_KEY:
        raise GeminiError("GEMINI_API_KEY 미설정. .env 또는 GitHub Secrets에 등록 필요.")

    # 429 로 죽을 때 다른 모델 버킷으로 한 번만 물러선다. 실패해도 원래 예외를
    # 그대로 올리므로 호출자의 기존 처리(대개 안전한 폴백)는 바뀌지 않는다.
    #
    # 왜 필요한가: 2026-08-22 브리핑에서 발송 직전 의미 dedup 이 429 로 죽고
    # `전량 유지` 로 물러나, 같은 사건(원안위 중저준위 표층처분시설 변경허가)이
    # 국내 8건 중 3건으로 나갔다. dedup 은 하루 두 번 부르는 호출인데 매시간
    # 크롤과 같은 flash 버킷을 쓴 탓에 저녁이면 늘 굶는다.
    if fallback_model and fallback_model != (model or MODEL):
        try:
            return call_json(
                system_prompt, user_message, temperature=temperature,
                max_output_tokens=max_output_tokens, timeout=timeout,
                retries=retries, thinking_budget=thinking_budget,
                model=model, fallback_model=None, label=label)
        except GeminiTruncated:
            # 잘림은 모델을 바꿔도 같은 자리에서 잘린다 — 입력을 줄이라는 신호다.
            raise
        except GeminiError as e:
            if "HTTP 429" not in str(e):
                raise
            print(f"[gemini] {model or MODEL} 쿼터 소진 → {fallback_model} 로 폴백 "
                  f"({label})")
            return call_json(
                system_prompt, user_message, temperature=temperature,
                max_output_tokens=max_output_tokens, timeout=timeout,
                retries=retries, thinking_budget=thinking_budget,
                model=fallback_model, fallback_model=None,
                label=f"{label}:fallback")

    generation_config: dict = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
        "responseMimeType": "application/json",
    }
    if thinking_budget is not None:
        generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

    url = _ENDPOINT.format(model=model or MODEL) + f"?key={API_KEY}"
    body = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "generationConfig": generation_config,
    }

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        # 재시도도 한 번의 호출이고 한도를 그만큼 깎는다. attempt 를 라벨에 실어야
        # "chunk 4회"가 실제로는 12회였다는 것이 보인다.
        _record_call(model or MODEL, label if attempt == 0 else f"{label}:retry")
        _pace()
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
            last_err = GeminiError(f"HTTP {e.code}: {body_text[:600]}")
            # 429/5xx만 재시도
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise last_err from e
            if e.code == 429 and _is_daily_quota(body_text):
                # 일일 한도는 재시도해도 오늘 안에 안 풀린다. 기다린 뒤 또 태우면
                # 한 번 실패한 호출이 쿼터를 4배로 먹고 잡 시간까지 잡아먹는다.
                # GeminiTruncated 와 같은 판단이다 — 같은 조건으로 다시 부르면
                # 같은 결과가 나오는 실패는 재시도 신호가 아니다.
                #
                # 판정을 반드시 찍는다. 2026-08-12 오디오 브리핑이 19초 만에
                # 죽었는데, 로그의 본문이 [:600] 으로 잘려 quotaId 가 안 보여
                # "일일이라 즉시 포기"인지 "분당인데 안 잤다"인지 사후에 못 갈랐다.
                # 같은 `limit: 20` 메시지가 크롤에선 재시도로 풀린다 — 두 갈래를
                # 가르는 건 본문 안의 표지뿐이고, 그 표지를 안 남기면 매번 다시 캔다.
                print(f"[gemini] 일일 한도 판정 — 재시도 없이 포기 "
                      f"({model or MODEL} / {label} / 표지 {_daily_quota_marker(body_text)})")
                raise last_err from e
            if e.code == 429:
                # 분당 한도. 서버가 알려주는 값을 그대로 쓴다 — 고정 사다리는
                # 서버 요구(실측 42초)보다 이른 20초에 첫 재시도를 내보내 실패가
                # 보장된 요청으로 한도를 더 깎는다.
                wait = _retry_delay_seconds(body_text) or 20 * (attempt + 1)
                global _slept_total
                if _slept_total + wait > RETRY_SLEEP_BUDGET_SEC:
                    print(f"[gemini] 429 재시도 예산 소진 "
                          f"({_slept_total:.0f}/{RETRY_SLEEP_BUDGET_SEC:.0f}초) — "
                          f"더 기다리지 않고 포기 ({model or MODEL} / {label})")
                    raise last_err from e
                _slept_total += wait
                print(f"[gemini] 429 분당 한도 — {wait:.0f}초 대기 후 재시도 "
                      f"{attempt + 1}/{retries} ({model or MODEL} / {label})")
                time.sleep(wait)
            else:
                time.sleep(2 ** attempt)
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
