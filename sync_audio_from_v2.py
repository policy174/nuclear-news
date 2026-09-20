"""v2 사이트에 올라온 오디오 브리핑을 가져와 우리 `days` 에 쌓는다.

왜 — 우리 오디오는 Gemini 429 로 이틀째 못 만들었다(2026-09-20 로그: 대본 생성이
세 모델 모두 429). v2 는 **별도 저장소라 별도 API 키**라서 같은 날 정상 생성됐다.
코드 차이가 아니라 쿼터가 갈려 있어서다.

**내용이 다르다는 것을 알고 쓴다.** 같은 09-20 을 대조하면 v2 3건은 인디애나
석탄 / RISE ASIA / 제12차 전기본이고 우리는 SMR 골든타임 / 대만 마안산 /
인디애나 석탄이다 — 겹치는 건 하나고 순위도 다르다. 즉 화면이 보여주는 뉴스와
음성이 읽는 뉴스가 어긋날 수 있다. 지니 판정(2026-09-21): "이슈 달라도 괜찮다".
근본 해결은 보조 Gemini 키로 우리 쪽 생성을 살리는 것이고, 이 스크립트는
그때까지의 다리다.

v2 는 **하루치만** 들고 있다 — Cloudflare Pages 가 배포마다 파일을 갈아서 과거
mp3 가 남지 않는다(실측: 09-21 만 200, 09-16~20 은 전부 404). 그래서 "며칠치를
한 번에" 는 불가능하고, 매일 돌려 우리 `days` 에 쌓는 수밖에 없다.

    python sync_audio_from_v2.py                 # 오늘치 받아 days 에 병합
    python sync_audio_from_v2.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
OUT_DIR = ROOT / "web" / "public" / "data" / "audio"
MANIFEST = OUT_DIR / "audio.json"
V2 = "https://nuclens-v2.pages.dev/data/audio"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
# 우리 화면이 읽는 창. 이보다 오래된 날짜는 재생 대상이 아니므로 파일도 지운다.
KEEP_DAYS = 14


def fetch(url: str, timeout: float = 30.0) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def load_manifest() -> dict:
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        remote = json.loads(fetch(f"{V2}/audio.json").decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — 오디오 부재는 비치명
        print(f"[audio-v2] manifest 를 못 읽었다: {type(exc).__name__}: {exc}")
        return 0

    date = str(remote.get("date") or "").strip()
    variants = remote.get("variants") or {}
    if not date or not variants:
        print("[audio-v2] 가져올 것이 없다")
        return 0

    ours = load_manifest()
    days = ours.get("days") if isinstance(ours.get("days"), dict) else {}
    # 우리가 직접 만든 그날 오디오가 있으면 그것이 이긴다 — 내용이 화면과 맞는
    # 쪽이 언제나 낫다. v2 는 빈자리만 메운다.
    if date in days:
        print(f"[audio-v2] {date} 는 이미 있다 — 건너뜀")
        return 0
    if str(ours.get("date") or "") == date:
        print(f"[audio-v2] {date} 는 우리가 만든 것이 있다 — 건너뜀")
        return 0

    print(f"[audio-v2] {date} · {len(variants)}종 가져온다")
    if args.dry_run:
        for key, v in variants.items():
            print(f"  {key}: {v.get('file')} ({v.get('duration_sec')}초)")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    got = {}
    for key, v in variants.items():
        name = str(v.get("file") or "").strip()
        if not name:
            continue
        try:
            blob = fetch(f"{V2}/{name}", timeout=120)
        except Exception as exc:  # noqa: BLE001
            print(f"  {key}: 받기 실패 — {type(exc).__name__}")
            continue
        (OUT_DIR / name).write_bytes(blob)
        got[key] = {k: v[k] for k in v if k != "telegram_sent_at"}
        print(f"  {key}: {name} {len(blob) // 1024}KB")

    if not got:
        print("[audio-v2] 받은 파일이 없다")
        return 0

    days[date] = got
    # 창 밖 날짜는 manifest 와 파일에서 같이 지운다 — manifest 만 지우면 저장소에
    # mp3 가 영영 쌓인다(하루 ~1.5MB).
    stale = sorted(days)[:-KEEP_DAYS]
    for old in stale:
        for v in (days.pop(old) or {}).values():
            path = OUT_DIR / str(v.get("file") or "")
            if path.name and path.exists():
                path.unlink()
    ours["days"] = days
    # 최신 날짜를 top-level 에도 반영한다 — 구버전 계약(date/file/variants)을
    # 읽는 경로가 화면에 남아 있다(audioVariantsFor 의 today 분기).
    newest = sorted(days)[-1]
    ours["date"] = newest
    ours["variants"] = days[newest]
    first = next(iter(days[newest].values()), {})
    for field in ("file", "duration_sec", "script_chars", "voices", "generated_at"):
        if first.get(field) is not None:
            ours[field] = first[field]
    ours["source"] = "nuclens-v2"
    MANIFEST.write_text(json.dumps(ours, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[audio-v2] days {len(days)}일치 · 최신 {newest}"
          + (f" · 창 밖 {len(stale)}일 삭제" if stale else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
