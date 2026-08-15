"""오디오 대본 prompt 품질 eval — 실제 LLM 으로 corpus 5종을 돌린다.

mock unit test 는 코드 분기만 검증한다. "전 이슈를 빠짐없이 지시한 분량으로
요약하는가"는 실제 모델이 지켜야 하는 계약이라 여기서 잰다. CI 아님 —
**main 병합 전 수동 1회 실행이 게이트다** (통과 전 병합 금지).

사용:
    python eval/run_script_eval.py            # corpus 전부
    python eval/run_script_eval.py normal     # 하나만

검사 (corpus 당):
  - generate_script 성공 = issue ID 완전성(누락·중복·창작·순서) 통과
  - 조립·프레임 후 전 라인 HOST: 형식, 오프닝·클로징 정확히 1회
  - 전환 라인은 rest 가 있을 때만
  - 섹션별 대사 분량이 예산의 [0.5, 1.4]× 안 (밖이면 실패, [0.8, 1.05]× 밖이면 경고)
  - 평시(normal) corpus 는 추정 duration 480~600초 (하드 게이트)
  - 제목 키워드 등장 크로스체크 (보조 — 경고만)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import audio_brief  # noqa: E402

CORPUS_DIR = Path(__file__).parent / "corpus"
# corpus 별 하드 duration 게이트 (초). None 이면 보고만.
DURATION_GATES = {"normal": (480, 600)}


def _keyword(title: str) -> str:
    import re
    words = [w.strip("'\"()[],.") for w in
             re.split(r"[\s·…—-]+", str(title))]
    words = [w for w in words if len(w) >= 2]
    return max(words, key=len) if words else ""


def run_corpus(name: str, path: Path) -> list[str]:
    """실패 사유 목록 (빈 목록 = 통과). 경고는 stdout 으로만."""
    failures: list[str] = []
    briefing, by_id = audio_brief.load_briefing(path)
    if not briefing:
        return [f"{name}: briefings.json 로드 실패"]
    deep_ids, rest_ids = audio_brief._issue_ids(briefing)
    deep_ids = [i for i in deep_ids if i in by_id]
    rest_ids = [i for i in rest_ids if i in by_id]
    deep_sec, rest_sec = audio_brief.section_budgets(len(deep_ids), len(rest_ids))
    print(f"── {name}: deep {len(deep_ids)} / rest {len(rest_ids)} "
          f"(예산 {deep_sec:.0f}s + {rest_sec:.0f}s)")

    # 섹션별 분량을 재려고 generate_section 을 감싼다
    sections: list[tuple[str, int]] = []
    original = audio_brief.generate_section

    def spy(system_prompt, material, expected_ids, **kwargs):
        lines, spoken = original(system_prompt, material, expected_ids, **kwargs)
        kind = "deep" if system_prompt is audio_brief.SYSTEM_PROMPT_DEEP else "rest"
        sections.append((kind, spoken))
        return lines, spoken

    audio_brief.generate_section = spy
    try:
        script = audio_brief.generate_script(briefing, by_id)
    except Exception as exc:  # noqa: BLE001 — 실패 사유가 곧 리포트
        return [f"{name}: generate_script 실패 — {exc}"]
    finally:
        audio_brief.generate_section = original

    framed = audio_brief.apply_frame(script, briefing)
    lines = framed.splitlines()

    # 형식: 전 라인 HOST:, 오프닝·클로징 1회
    bad = [l for l in lines if not l.startswith("HOST: ")]
    if bad:
        failures.append(f"{name}: HOST: 형식 아닌 줄 {len(bad)}개 — {bad[0][:40]}")
    opening, closing = audio_brief.frame_lines(briefing)
    if lines[0] != opening or lines.count(opening) != 1:
        failures.append(f"{name}: 오프닝이 정확히 1회 첫 줄이 아님")
    if lines[-1] != closing or lines.count(closing) != 1:
        failures.append(f"{name}: 클로징이 정확히 1회 마지막 줄이 아님")

    # 전환 라인: rest 있을 때만
    has_transition = audio_brief.TRANSITION_LINE in lines
    if bool(rest_ids) and bool(deep_ids) != has_transition:
        failures.append(f"{name}: 전환 라인 유무가 rest({len(rest_ids)}건)와 안 맞음")

    # 섹션별 분량
    budgets = {"deep": deep_sec * audio_brief.CHARS_PER_SEC,
               "rest": rest_sec * audio_brief.CHARS_PER_SEC}
    for kind, spoken in sections:
        high = budgets[kind]
        if not high:
            continue
        ratio = spoken / high
        mark = "OK" if 0.8 <= ratio <= 1.05 else "경고"
        print(f"   {kind}: {spoken}자 / 예산 {high:.0f}자 (×{ratio:.2f}) {mark}")
        if not (0.5 <= ratio <= 1.4):
            failures.append(f"{name}: {kind} 분량 ×{ratio:.2f} — 예산 이탈")

    # 추정 duration
    total_spoken = sum(len(l.split(": ", 1)[1]) for l in lines)
    est_sec = total_spoken / audio_brief.CHARS_PER_SEC
    print(f"   합계 {total_spoken}자 → 추정 {est_sec:.0f}초 ({est_sec / 60:.1f}분)")
    gate = DURATION_GATES.get(name)
    if gate and not (gate[0] <= est_sec <= gate[1]):
        failures.append(f"{name}: 추정 duration {est_sec:.0f}초 — 게이트 {gate} 이탈")

    # 보조: 제목 키워드 크로스체크 (경고만 — ID 검증이 본 게이트)
    for issue_id in deep_ids + rest_ids:
        keyword = _keyword(by_id[issue_id].get("title", ""))
        if keyword and keyword not in framed:
            print(f"   경고: {issue_id} 제목 키워드 '{keyword}' 미등장")
    return failures


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    names = [only] if only else sorted(p.name for p in CORPUS_DIR.iterdir()
                                       if p.is_dir())
    all_failures: list[str] = []
    for index, name in enumerate(names):
        if index:
            # 크롤 체인과 같은 키의 분당 한도를 나눠 쓴다 — 몰아치면 서로 죽는다
            print("   (분당 한도 창 회복 대기 70초)")
            import time
            time.sleep(70)
        all_failures.extend(run_corpus(name, CORPUS_DIR / name))
    print()
    if all_failures:
        for failure in all_failures:
            print(f"FAIL {failure}")
        print(f"\n결론: 실패 {len(all_failures)}건 — main 병합 금지")
        return 1
    print(f"결론: corpus {len(names)}종 전부 통과 — 병합 게이트 해제")
    return 0


if __name__ == "__main__":
    sys.exit(main())
