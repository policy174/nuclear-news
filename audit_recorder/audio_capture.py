"""국회 의사중계 소리만 10분 조각으로 저장 — 두 번째 음성인식(Whisper) 대조용 (2026-10-22, audio_stt/KICKOFF.md).

  python audio_capture.py --xcode 55 --minutes 345 --out audio
조각 파일명 = 그 조각이 시작된 한국 시각(TZ=Asia/Seoul 필요) → 국회 자막 시각과 바로 맞춘다.
생중계가 아닐 땐 30초마다 다시 보고, ffmpeg 가 끊기면 다시 붙는다. 실시간 요약과는 무관한 별도 프로세스.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

from recorder import live_items, pick_live, play_info

REFERER = "https://assembly.webcast.go.kr/"


def hls_url(item):
    info = play_info(item["xcode"], item["xcgcd"])
    hls = info.get("xhls") or []
    if isinstance(hls, list) and hls:
        hls = hls[0]
    if isinstance(hls, dict):
        return hls.get("240p") or hls.get("default")
    return hls or None


def main():
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--xcode", required=True)
    ap.add_argument("--minutes", type=float, default=345)
    ap.add_argument("--out", default="audio")
    ap.add_argument("--force", action="store_true", help="시험: 생중계가 아니어도 대기 화면 소리를 받는다")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + a.minutes * 60
    while time.time() < deadline:
        try:
            item = pick_live(a.xcode)
            if not item and a.force:
                item = next((i for i in live_items() if str(i.get("xcode")) == str(a.xcode) and i.get("xcgcd")), None)
            url = hls_url(item) if item else None
        except Exception as e:
            print("중계 조회 실패", repr(e)[:150], flush=True)
            url = None
        if not url:
            time.sleep(30)
            continue
        print("소리 수집 시작", url, flush=True)
        left = max(60, int(deadline - time.time()))
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-headers", f"Referer: {REFERER}\r\n",
               "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "30",
               "-i", url, "-t", str(left), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k",
               "-f", "segment", "-segment_time", "600", "-strftime", "1", "-reset_timestamps", "1",
               str(out / f"{a.xcode}_%Y%m%d_%H%M%S.opus")]
        r = subprocess.run(cmd)
        print("ffmpeg 종료", r.returncode, flush=True)
        time.sleep(10)


if __name__ == "__main__":
    main()
