"""소리 조각 → Whisper 두 번째 자막 (faster-whisper, CPU int8). audio_stt/KICKOFF.md.

  python audio_stt.py --audio audio --out stt --shard 0 --shards 8
  python audio_stt.py --merge stt --out stt_all.txt
조각 파일명(55_20261022_101000.opus)의 시작 시각 + 문장 오프셋 = 한국 시각. 국회 자막과 같은 시계로 맞춘다.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

# 고유명사·용어를 앞에 깔아 두면 오인식이 준다(Whisper initial_prompt). 지어낸 문장 말고 실제 쓰는 낱말만.
PROMPT = ("국정감사 질의응답. 한국수력원자력 한수원 김회천 사장, 산업통상자원부, 기후에너지환경부, 원자력안전위원회, "
          "계속운전, 신규원전, 소형모듈원자로 SMR, 사용후핵연료, 고준위 방사성폐기물, 건식저장시설, 전력수급기본계획 전기본, "
          "한빛, 한울, 고리, 월성, 새울, 신한울, 체코 두코바니, 원전 수출, 전력망, 데이터센터, 재생에너지, 해상풍력.")
NAME = re.compile(r"_(\d{8})_(\d{6})\.opus$")


def start_of(p: Path):
    m = NAME.search(p.name)
    return dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S") if m else None


def transcribe(audio: Path, out: Path, shard: int, shards: int, model_name: str):
    from faster_whisper import WhisperModel
    files = sorted(p for p in audio.rglob("*.opus") if start_of(p))
    mine = [p for i, p in enumerate(files) if i % shards == shard]
    print(f"조각 {len(files)}개 중 {len(mine)}개 담당", flush=True)
    if not mine:
        return
    model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 4)
    out.mkdir(parents=True, exist_ok=True)
    for p in mine:
        t0 = start_of(p)
        dst = out / (p.stem + ".jsonl")
        # PyAV 버전 충돌(10-22 러너: open() metadata_errors) → ffmpeg 로 직접 16kHz float 로 풀어 넘긴다
        import subprocess, numpy as np
        raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(p), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
                             capture_output=True, check=True).stdout
        segs, info = model.transcribe(np.frombuffer(raw, dtype=np.float32), language="ko", beam_size=5, vad_filter=True,
                                      initial_prompt=PROMPT, condition_on_previous_text=False)
        n = 0
        with dst.open("w", encoding="utf-8") as f:
            for s in segs:
                a, b = t0 + dt.timedelta(seconds=s.start), t0 + dt.timedelta(seconds=s.end)
                f.write(json.dumps({"start": a.strftime("%H:%M:%S"), "end": b.strftime("%H:%M:%S"),
                                    "text": s.text.strip(), "logprob": round(s.avg_logprob, 3)}, ensure_ascii=False) + "\n")
                n += 1
        print(p.name, "문장", n, flush=True)


def merge(stt: Path, out: Path):
    rows = []
    for p in sorted(stt.rglob("*.jsonl")):
        date = NAME.search(p.stem + ".opus").group(1) if NAME.search(p.stem + ".opus") else ""
        for line in p.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            rows.append((date, r["start"], r["text"]))
    rows.sort()
    out.write_text("Whisper 두 번째 자막(대조용, 정답 아님)\n\n" + "\n".join(f"[{t[:5]}] {x}" for _, t, x in rows), encoding="utf-8")
    print("문장", len(rows), "→", out, flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio")
    ap.add_argument("--out", required=True)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--merge")
    a = ap.parse_args()
    if a.merge:
        merge(Path(a.merge), Path(a.out))
    else:
        transcribe(Path(a.audio), Path(a.out), a.shard, a.shards, a.model)
