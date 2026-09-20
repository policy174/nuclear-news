"""Central task policy for production Gemini calls.

This module deliberately contains no domain imports.  A policy entry describes the
task's model and reasoning contract; it does not own quota, retries, prompts, or
failure fallbacks.  The initial policy is a request-body no-op: every production
task leaves thinking unspecified and keeps explicit caller sampling.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

import gemini_client

SIMPLE_EXTRACT = "SIMPLE_EXTRACT"
BULK_CURATION = "BULK_CURATION"
IDENTITY_REVIEW = "IDENTITY_REVIEW"
CONTEXT_SYNTHESIS = "CONTEXT_SYNTHESIS"
NARRATIVE_GENERATION = "NARRATIVE_GENERATION"
FINAL_SEMANTIC_VERIFY = "FINAL_SEMANTIC_VERIFY"


@dataclass(frozen=True)
class TaskProfile:
    task: str
    model_resolver: Callable[[], str]
    thinking_level: str | None = None
    sampling_mode: str = "explicit"
    strict_reasoning: bool = False
    activation: str = "IMPLEMENTED_BUT_NOT_ACTIVATED"

    def model(self) -> str:
        return self.model_resolver()

    def reasoning_kwargs(self) -> dict[str, str]:
        """Only emit a keyword when a level is explicitly activated."""
        if self.thinking_level is None:
            return {}
        return {"thinking_level": self.thinking_level}


def _main_model() -> str:
    return gemini_client.MODEL or "gemini-3.1-flash-lite"


def _synthesis_model() -> str:
    return gemini_client.synthesis_model()


def _review_model() -> str:
    return gemini_client._resolve(
        "GEMINI_REVIEW_MODEL", "gemini-3.5-flash-lite") or "gemini-3.5-flash-lite"


def _insight_model() -> str:
    return gemini_client._resolve(
        "GEMINI_INSIGHT_MODEL", "gemini-3.5-flash-lite") or "gemini-3.5-flash-lite"


def _script_model() -> str:
    return gemini_client._resolve(
        "GEMINI_SCRIPT_MODEL", "gemini-3.5-flash-lite") or "gemini-3.5-flash-lite"


def _card_narrator_model() -> str:
    """카드 편집 데스크. **무엇을 말할지** 고르는 자리라 한 단 위를 쓴다."""
    return gemini_client._resolve(
        "CARD_EDITORIAL_NARRATOR_MODEL", _synthesis_model()) or _synthesis_model()


def _card_writer_model() -> str:
    """카드 카피라이터. Narrator 가 고른 것을 규격에 맞게 적는 자리다.

    예전 스토리 카드는 `gemini-3-flash-preview` 를 기본값으로 박아 뒀는데,
    그 선택의 근거는 워크플로 주석의 "로컬 실측에서 기본 flash-lite 가 표지
    제목 길이로 세 번 연속 걸렸다" 한 줄뿐이었다. 길이 문제는 모델을 올려
    푸는 문제가 아니라 재시도에 실패 사유를 돌려주면 되는 문제다(일일 카드는
    그렇게 한다). 카드 기본선(`CARDS_GEMINI_MODEL` → `GEMINI_MODEL`)으로
    되돌린다.
    """
    # 이 저장소로 들여오며 기본선을 한 단 내렸다. v2 에서는 narrator 와 writer 가
    # 서로 다른 모델(3.5-lite / 3.1-lite)이라 두 자리가 자연히 갈렸는데, 여기서는
    # synthesis_model() 이 MODEL 로 되돌려져 있어 그대로 두면 **둘이 같은 모델**이
    # 되고 "편집 판단은 한 단 위" 라는 이 표의 전제가 조용히 사라진다.
    # 받아쓰기 자리는 이 저장소가 이미 굶었을 때 물러서는 lite 를 기본으로 쓴다
    # (GEMINI_FALLBACK_MODEL 체인의 첫 칸). 모델을 새로 들이지 않는다.
    _writer_default = gemini_client.FALLBACK_MODEL or _main_model()
    return gemini_client._resolve(
        "CARD_WRITER_MODEL",
        gemini_client._resolve("CARDS_GEMINI_MODEL", _writer_default) or _writer_default,
    ) or _writer_default


def _verify_model() -> str:
    # Independent selection is available for offline Semantic Gold evaluation.
    # The unset production default remains the current curation model.
    return gemini_client._resolve("GEMINI_VERIFY_MODEL", _main_model()) or _main_model()


def _entry(task: str, resolver: Callable[[], str], *, strict: bool = False) -> TaskProfile:
    return TaskProfile(task=task, model_resolver=resolver, strict_reasoning=strict)


_PROFILES: dict[str, TaskProfile] = {
    "curation": _entry(BULK_CURATION, _main_model),
    "issue_review": _entry(IDENTITY_REVIEW, _review_model),
    # 장기 스토리 판정. `issue_review` 와 **같은 과제가 아니다** — 저쪽은
    # "같은 사건인가", 이쪽은 "같은 이야기의 다른 단계인가"를 묻는다.
    # 모델 버킷은 같이 쓴다(둘 다 짧은 판정 한 줄).
    "thread_judge": _entry(IDENTITY_REVIEW, _review_model),
    "keei_match": _entry(IDENTITY_REVIEW, _main_model),
    "dedup": _entry(IDENTITY_REVIEW, _main_model),
    "dedup_final": _entry(IDENTITY_REVIEW, _main_model),
    "issue_insight": _entry(CONTEXT_SYNTHESIS, _insight_model),
    "daily_brief": _entry(CONTEXT_SYNTHESIS, _main_model),
    "daily_brief_implication": _entry(CONTEXT_SYNTHESIS, _synthesis_model),
    "daily_brief_report": _entry(CONTEXT_SYNTHESIS, _synthesis_model),
    "trend_insights": _entry(CONTEXT_SYNTHESIS, _synthesis_model),
    "weekly_bot": _entry(CONTEXT_SYNTHESIS, _synthesis_model),
    "daily_lead": _entry(NARRATIVE_GENERATION, _synthesis_model),
    # 이슈 카드의 표시 제목. **신원이 아니다** — `issue.title` 은 그대로 두고
    # 화면이 읽을 칸을 따로 만든다(`issue_headline` 의 docstring).
    "issue_headline": _entry(NARRATIVE_GENERATION, _synthesis_model),
    "audio_brief": _entry(NARRATIVE_GENERATION, _script_model),
    # 카드뉴스. 네 자리로 나눈 이유는 **하는 일이 다르기** 때문이다.
    #
    #   card_editorial_narrator  무엇을 말할 것인가 (편집 판단)
    #   card_writer              그것을 어떻게 적을 것인가 (카피)
    #   card_daily_writer        스토리 없는 날 — 위 둘을 한 응답에서
    #   card_writer_repair       걸린 곳만 다시 (재해석 금지)
    #
    # 스토리가 없는 날까지 호출을 둘로 늘리지 않는다. 그런 날은 편집 판단의
    # 재료가 오늘치 3건뿐이라 한 응답 안에서 판단과 카피를 같이 받아도
    # 맥락이 끊기지 않는다.
    "card_editorial_narrator": _entry(CONTEXT_SYNTHESIS, _card_narrator_model),
    "card_writer": _entry(NARRATIVE_GENERATION, _card_writer_model),
    "card_daily_writer": _entry(NARRATIVE_GENERATION, _card_writer_model),
    "card_writer_repair": _entry(NARRATIVE_GENERATION, _card_writer_model),
    "pubs_translate": _entry(SIMPLE_EXTRACT, _main_model),
    "expert_dossiers": _entry(SIMPLE_EXTRACT, _main_model),
    "expert_plan": _entry(CONTEXT_SYNTHESIS, _synthesis_model),
    "expert_script": _entry(NARRATIVE_GENERATION, _synthesis_model),
    "expert_repair": _entry(NARRATIVE_GENERATION, _synthesis_model),
    "expert_reorder": _entry(NARRATIVE_GENERATION, _synthesis_model),
    "expert_intro_repair": _entry(NARRATIVE_GENERATION, _synthesis_model),
    "expert_verify": _entry(FINAL_SEMANTIC_VERIFY, _verify_model, strict=True),
    "fast_verify": _entry(FINAL_SEMANTIC_VERIFY, _verify_model, strict=True),
    "fast_semantic_repair": _entry(NARRATIVE_GENERATION, _script_model),
}


def _canonical_name(name: str) -> str:
    for prefix in (
        "expert_verify_after_repair", "expert_verify", "expert_dossiers",
        "expert_script_retry", "expert_script", "expert_intro_repair",
        "expert_repair", "expert_reorder",
    ):
        if name.startswith(prefix):
            return "expert_script" if prefix == "expert_script_retry" else prefix.replace(
                "expert_verify_after_repair", "expert_verify")
    return name


def profile(name: str) -> TaskProfile:
    canonical = _canonical_name(name)
    try:
        return _PROFILES[canonical]
    except KeyError as exc:
        raise KeyError(f"등록되지 않은 LLM task profile: {name}") from exc


def generation_policy_fingerprint(task_profile: TaskProfile, prompt_version: int | str) -> str:
    effective = (f"level:{task_profile.thinking_level}"
                 if task_profile.thinking_level is not None else "unspecified")
    raw = "|".join((task_profile.model(), effective,
                    task_profile.sampling_mode, str(prompt_version)))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# reasoning 결정을 묶어 둘 계약 지문.
#
# `generation_policy_fingerprint()` 로는 부족하다. 그건 model/thinking/sampling/
# prompt_version 네 개만 본다 — parser 나 response schema 가 바뀌어도, batch 크기가
# 바뀌어도 값이 그대로다. 그러면 "이 설정은 검증됐다"는 판정이 검증한 적 없는 계약
# 위에서 조용히 계속 유효해 보인다.
#
# 필드를 하나라도 빠뜨리면 지문을 만들지 않는다. 부분 계약으로 만든 지문은 없는
# 것보다 나쁘다 — 빠진 축이 바뀌어도 같은 값이 나오므로 안전하다고 착각하게 된다.
CONTRACT_FIELDS = (
    "profile",
    "resolved_model",
    # 이름이 아니라 **실제 직렬화된 값**이어야 한다. 상수 "unspecified" 를 적으면
    # 3.1 의 explicit OFF 와 3.5 의 필드 생략이 같은 지문이 된다(tools/observed_baseline.py).
    "observed_baseline_thinking",
    "system_prompt_sha",
    "user_builder_sha",
    "response_schema_sha",
    "temperature",
    "max_output_tokens",
    "timeout",
    "retries",
    "batch_size",
    "split_budget",
    "parser_sha",
    "normalizer_sha",
)


def production_contract_fingerprint(contract: dict[str, object]) -> str:
    missing = [field for field in CONTRACT_FIELDS if field not in contract]
    if missing:
        raise KeyError(
            "production contract fingerprint needs every field; missing: "
            + ", ".join(missing))
    if str(contract["observed_baseline_thinking"]) in {"unspecified", "none", ""}:
        raise ValueError(
            "observed_baseline_thinking must be the serialized value "
            "(absent / budget:N / level:X), not an abstract name")
    raw = "|".join(f"{field}={contract[field]!r}" for field in CONTRACT_FIELDS)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def production_policy_snapshot() -> dict[str, dict[str, object]]:
    return {
        name: {
            "task": item.task,
            "model": item.model(),
            "thinking": item.thinking_level,
            "sampling_mode": item.sampling_mode,
            "strict_reasoning": item.strict_reasoning,
            "activation": item.activation,
        }
        for name, item in sorted(_PROFILES.items())
    }
