#!/usr/bin/env python
"""국감 라이브 자막 레코더 v1 — 코드는 기억하고, AI는 판단한다.

사용:
  python recorder.py                      # 생중계 자동 감지(산자중기위 우선), 자막 소켓 수신
  python recorder.py --xcode 55           # 특정 위원회만 (live_list.asp 의 xcode)
  python recorder.py --any                # 아무 생중계나 (9/28 실측용)
  python recorder.py --replay <raw_events.jsonl>   # RAW 로 transcript/md 재생성

출력: 2026-국정감사/<날짜>_<위원회>/ raw_events.jsonl · transcript.jsonl · 오늘_국감.md · alerts.jsonl · meta.json
"""
import argparse, datetime as dt, html, json, os, re, sys, threading, time
from pathlib import Path

import requests

BASE = "https://assembly.webcast.go.kr/main/"
HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/145", "Referer": BASE}
ROOT = Path(__file__).resolve().parent / "2026-국정감사"
IDLE_FINAL_SEC = 2.0      # 세그먼트가 이만큼 안 바뀌면 확정
RENDER_EVERY_SEC = 30
PLAIN_SMI_RE = re.compile(r"smi(-hy|-dw)?\d?\.webcast\.go\.kr", re.I)   # 플레이어 코드와 동일: 매치=일반 자막(socket.io), 아니면 AI 자막(WebSocket)

# Recall > Precision. AI 음성인식 자막이라 띄어쓰기 변형 허용.
POLICY_TAGS = {  # N2 하위태그 — "공론화 관련 발언만" 같은 사후 질의용
    "신규원전": r"신규\s*원전|신규\s*건설|추가\s*원전|대형\s*원전\s*건설",
    "계속운전": r"계속\s*운전|수명\s*연장|설계\s*수명|운영\s*허가\s*갱신",
    "공론화·수용성": r"공론화|주민\s*수용성|주민\s*동의|지역\s*수용성|사회적\s*합의",
    "전기본·에너지믹스": r"전력\s*수급|전기본|에너지\s*믹스|원전\s*비중|탈\s*원전|원전\s*정책|원자력\s*정책|원자력\s*진흥",
    "원전수출": r"원전\s*수출|원전\s*수주|팀\s*코리아|체코\s*원전|웨스팅",
    "SMR": r"S\s*M\s*R|소형\s*모듈|i\s*-?\s*SMR",
    "사용후핵연료·고준위": r"사용\s*후\s*핵연료|고준위|방폐|중간\s*저장|영구\s*처분|특별법",
    "규제·안전": r"원안위|원자력\s*안전|규제\s*체계|안전\s*규제|원전\s*해체",
    "예산": r"원전\s*예산|원자력\s*예산|R\s*&\s*D\s*예산",
    "인력·공급망": r"원전\s*인력|원전\s*생태계|원전\s*공급망|협력\s*업체|기자재",
    "핵비확산·수출통제": r"핵\s*비확산|수출\s*통제|한미\s*원자력\s*협정|농축|재처리",
}
KW_POLICY = {k: re.compile(v, re.I) for k, v in POLICY_TAGS.items()}
KW_RELATED = re.compile(r"한\s*수원|한국\s*수력|수력\s*원자력|K\s*H\s*N\s*P|고리|한울|한빛|월성|새울|신\s*한울|신\s*고리|APR\s*-?\s*1400|i\s*-?\s*SMR|원전|원자력|원자로|핵연료|두산\s*에너빌|팀\s*코리아", re.I)
KW_COMPANY = re.compile(r"한\s*수원|한국\s*수력|수력\s*원자력|K\s*H\s*N\s*P", re.I)


def now_iso():
    return dt.datetime.now().isoformat(timespec="milliseconds")


def classify(text):
    """→ (category, topics). N0 GENERAL / N1 NUCLEAR_RELATED / N2 NUCLEAR_POLICY(topics=하위태그). 키워드 1차, AI 의미판정은 v2."""
    pol = [tag for tag, rx in KW_POLICY.items() if rx.search(text)]
    rel = sorted({m.group(0).replace(" ", "") for m in KW_RELATED.finditer(text)})
    if pol:
        return "NUCLEAR_POLICY", pol
    if rel:
        return "NUCLEAR_RELATED", rel
    return "GENERAL", []


class Recorder:
    """소켓 메시지 → raw_events.jsonl(전부) → 세그먼트 확정 → 턴(발언) → transcript.jsonl / 오늘_국감.md"""

    def __init__(self, outdir: Path, meta: dict, clock=time.time):
        self.outdir = outdir
        outdir.mkdir(parents=True, exist_ok=True)
        self.meta = meta
        self.clock = clock
        self.raw = open(outdir / "raw_events.jsonl", "a", encoding="utf-8")
        self.segs = {}        # segment id → {"first","last","lines":[[hwa,text],...]}
        self.turns = []       # 닫힌 턴
        self.cur = None       # 열린 턴
        self.turn_seq = 0
        self.lock = threading.Lock()
        (outdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    # ---- 수신 ----
    def on_ai_message(self, data: str, ts=None):
        ts = ts or now_iso()
        self._raw({"captured_at": ts, "mode": "ai", "raw": data})
        try:
            msg = json.loads(data)
        except Exception:
            return
        seg = msg.get("segment")
        if seg is None:
            return
        lines = msg.get("transcripts") or [[None, msg.get("transcript", "")]]
        with self.lock:
            s = self.segs.setdefault(seg, {"first": ts, "lines": []})
            s["last"] = ts
            s["last_mono"] = self.clock()
            s["lines"] = [[l[0], str(l[1])] for l in lines if isinstance(l, (list, tuple)) and len(l) >= 2]
            s["final"] = msg.get("final")

    def on_plain_message(self, data, ts=None):
        """일반(속기) 자막: 갱신 없이 한 줄이 한 이벤트. 화자 신호 없음."""
        ts = ts or now_iso()
        self._raw({"captured_at": ts, "mode": "plain", "raw": data})
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(data))).split())
        if not text:
            return
        with self.lock:
            self._append_line(ts, None, text)

    def _raw(self, ev):
        self.raw.write(json.dumps(ev, ensure_ascii=False) + "\n")
        self.raw.flush()

    # ---- 확정 ----
    def flush(self, force=False):
        """안 바뀐 지 IDLE_FINAL_SEC 지난 세그먼트를 순서대로 턴에 붙인다. force: 전부."""
        with self.lock:
            now = self.clock()
            for seg in sorted(self.segs, key=lambda k: (str(type(k)), k)):
                s = self.segs[seg]
                if force or now - s["last_mono"] >= IDLE_FINAL_SEC or s.get("final") in (1, True, "1", "true"):
                    for hwa, text in s["lines"]:
                        text = text.strip()
                        if not text:
                            continue
                        new_speaker = text.startswith("-")        # 플레이어 코드: '-' 접두 = 화자 전환
                        if new_speaker:
                            text = text.lstrip("-").strip()
                        self._append_line(s["last"], hwa, text, new_speaker=new_speaker, segment=seg)
                    del self.segs[seg]
            if force and self.cur:
                self._close_turn()

    def _append_line(self, ts, hwa, text, new_speaker=False, segment=None):
        if self.cur is None or new_speaker:
            self._close_turn()
            self.turn_seq += 1
            self.cur = {"turn": self.turn_seq, "start_time": ts, "end_time": ts,
                        "speaker_live": f"UNKNOWN_{self.turn_seq:03d}",   # OCR 이 잡히면 이름 + status=estimated (v2)
                        "speaker_status": "unknown", "speaker_final": None, "speaker_source": None,
                        "speaker": f"UNKNOWN_{self.turn_seq:03d}",         # 표시용 = final or live
                        "hwa": hwa, "text": "", "segments": [], "alerted": False}
        self.cur["end_time"] = ts
        self.cur["text"] = (self.cur["text"] + " " + text).strip()
        if segment is not None:
            self.cur["segments"].append(segment)
        self._maybe_alert(self.cur)

    def _close_turn(self):
        if not self.cur or not self.cur["text"]:
            self.cur = None
            return
        t = self.cur
        t["category"], t["topics"] = classify(t["text"])
        t["company_related"] = bool(KW_COMPANY.search(t["text"]))
        self.turns.append(t)
        with open(self.outdir / "transcript.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({k: v for k, v in t.items() if k != "alerted"}, ensure_ascii=False) + "\n")
        self.cur = None

    # ---- 알림 ----
    def _maybe_alert(self, t):
        if t["alerted"]:
            return
        cat, topics = classify(t["text"])
        if cat != "NUCLEAR_POLICY" and not KW_COMPANY.search(t["text"]):
            return
        t["alerted"] = True
        ev = {"at": now_iso(), "turn": t["turn"], "start_time": t["start_time"], "speaker": t["speaker"],
              "category": cat, "topics": topics, "text": t["text"]}
        with open(self.outdir / "alerts.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        send_telegram(f"🔴 국감 자막 알림 {t['start_time'][11:19]} {t['speaker']}\n[{cat} / {' · '.join(topics)}]\n\n{t['text'][:800]}\n\n{self.meta.get('title', '')}")

    # ---- 문서 ----
    def render(self):
        with self.lock:
            turns = list(self.turns) + ([dict(self.cur)] if self.cur and self.cur["text"] else [])
        for t in turns:
            t.setdefault("category", classify(t["text"])[0])
            t.setdefault("topics", classify(t["text"])[1])
        mark = {"NUCLEAR_POLICY": "🔴", "NUCLEAR_RELATED": "🟡", "GENERAL": "⚪"}
        hm = lambda s: s[11:19] if len(s) >= 19 else s
        L = [f"# {self.meta.get('date', '')} {self.meta.get('title', '')} 모니터링", "",
             f"자동 갱신 {now_iso()[11:19]} · 발언 {len(turns)}건 · 🔴 원자력 정책 / 🟡 원자력 관련 / ⚪ 일반", "",
             "## 1. 🔴 원자력 정책 발언", "", "| 시각 | 발언자 | 키워드 | 발언 |", "|---|---|---|---|"]
        L += [f"| {hm(t['start_time'])} | {t['speaker']} | {' · '.join(t['topics'])} | {t['text'][:120]} |"
              for t in turns if t["category"] == "NUCLEAR_POLICY"] or ["| | | | (없음) |"]
        L += ["", "## 2. 🟡 원자력 관련 발언 (정책 포함, 시간순)", ""]
        L += [f"- **{hm(t['start_time'])} {t['speaker']}** {mark[t['category']]} {' · '.join(t['topics'])}\n  > {t['text']}"
              for t in turns if t["category"] != "GENERAL"] or ["(없음)"]
        L += ["", "## 3. 발언자별", ""]
        by = {}
        for t in turns:
            by.setdefault(t["speaker"], []).append(t)
        for sp, ts_ in by.items():
            L.append(f"### {sp}")
            L += [f"- {hm(t['start_time'])}~{hm(t['end_time'])} {mark[t['category']]} {t['text']}" for t in ts_]
            L.append("")
        L += ["## 4. 전체 스크립트", ""]
        L += [f"[{hm(t['start_time'])}] {t['speaker']} {mark[t['category']]}\n{t['text']}\n" for t in turns]
        (self.outdir / "오늘_국감.md").write_text("\n".join(L), encoding="utf-8")
        with open(self.outdir / "transcript.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(f"[{hm(t['start_time'])}] {t['speaker']}\n{t['text']}\n" for t in turns))


def load_env(path=Path(__file__).resolve().parents[1] / "nuclear-news-bot" / ".env"):
    """뉴스봇 .env 의 텔레그램 키 재사용. OPS 챗이 없으면 개인 챗으로."""
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() and v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"'))
    os.environ.setdefault("TELEGRAM_OPS_CHAT_ID", os.environ.get("TELEGRAM_CHAT_ID", ""))


SOURCE = "GH" if os.environ.get("GITHUB_ACTIONS") else "PC"   # PC·Actions 를 같이 돌리면 알림이 두 번 온다 — 출처 표시


def send_telegram(text, doc=None):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_OPS_CHAT_ID")
    if not (tok and chat):
        return
    try:
        if doc:
            with open(doc, "rb") as f:
                requests.post(f"https://api.telegram.org/bot{tok}/sendDocument", data={"chat_id": chat, "caption": f"[{SOURCE}] {text}"},
                              files={"document": f}, timeout=60)
        else:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": f"[{SOURCE}] {text}"}, timeout=10)
    except Exception as e:
        print("telegram 실패", e, file=sys.stderr)


# ---- 국회 중계 API ----
def live_items():
    r = requests.get(BASE + "service/live_list.asp", params={"vv": int(time.time())}, headers=HDRS, timeout=15)
    return r.json().get("xlist", [])


def pick_live(xcode=None, any_=False):
    live = [i for i in live_items() if i.get("xcgcd") and str(i.get("xstat")) != "0"]
    if xcode:
        live = [i for i in live if str(i.get("xcode")) == str(xcode)]
    elif not any_:
        live.sort(key=lambda i: 0 if i.get("xname") == "산자중기위" else 1)
    return live[0] if live else None


def play_info(xcode, xcgcd):
    h = {**HDRS, "Referer": BASE + f"player.asp?xcode={xcode}&xcgcd={xcgcd}"}
    return requests.get(BASE + "service/live_play.asp", params={"xcode": xcode, "xcgcd": xcgcd, "vv": int(time.time())}, headers=h, timeout=15).json()


def caption_endpoint(xsami: str):
    """플레이어 smi_on() 과 동일한 분기. → (mode, url)"""
    xsami = (xsami or "").strip()
    if not xsami:
        return None, None
    if PLAIN_SMI_RE.search(xsami):
        return "plain", xsami
    ws = re.sub(r"^http", "ws", xsami)
    return "ai", ws.rstrip("/") + "/hls"


def run_ai(url, rec: Recorder, stop):
    import websocket
    app = websocket.WebSocketApp(url, subprotocols=["echo-protocol"],
                                 on_message=lambda w, m: rec.on_ai_message(m),
                                 on_open=lambda w: print("ws open", url),
                                 on_error=lambda w, e: print("ws error", e, file=sys.stderr),
                                 on_close=lambda w, c, m: print("ws close", c, m))
    app.run_forever(ping_interval=25, ping_timeout=10)


def run_plain(url, rec: Recorder, stop):
    import socketio
    sio = socketio.Client(reconnection=False)
    sio.on("receive message", lambda msg: rec.on_plain_message(msg))
    sio.connect(url, transports=["websocket", "polling"])
    while not stop.is_set() and sio.connected:
        time.sleep(1)
    sio.disconnect()


def record(args):
    recs = {}   # outdir → Recorder. 재접속해도 같은 회의면 턴·문서를 이어 쓴다
    while True:
        try:
            item = pick_live(args.xcode, args.any)
            info = play_info(item["xcode"], item["xcgcd"]) if item else None
        except Exception as e:   # 국회 서버 타임아웃 한 번에 레코더가 죽으면 안 된다
            print(now_iso(), "목록 조회 실패, 30초 후 재시도", repr(e)[:150])
            time.sleep(30)
            continue
        if not item:
            print(now_iso(), "생중계 없음, 60초 후 재확인")
            time.sleep(60)
            continue
        mode, url = caption_endpoint(info.get("xsami", ""))
        title = f"{item.get('xname', '')} {info.get('xsubj') or item.get('xsubj') or ''}".strip()
        meta = {"date": dt.date.today().isoformat(), "title": title, "item": item, "play": info, "mode": mode, "url": url,
                "source": BASE + f"player.asp?xcode={item['xcode']}&xcgcd={item['xcgcd']}"}
        outdir = ROOT / f"{meta['date']}_{re.sub(r'[^\w가-힣]+', '_', item.get('xname', 'live'))}"
        print(now_iso(), "생중계:", title, "| 자막모드:", mode, url)
        if not url:
            print("자막 서버 없음(xsami 빈값) — 60초 후 재시도")
            time.sleep(60)
            continue
        if outdir not in recs:
            recs[outdir] = Recorder(outdir, meta)
            send_telegram(f"국감 자막 수신 시작: {title} ({mode})")
        rec = recs[outdir]
        stop = threading.Event()

        def ticker():
            while not stop.is_set():
                time.sleep(RENDER_EVERY_SEC)
                rec.flush()
                rec.render()
        threading.Thread(target=ticker, daemon=True).start()
        try:
            (run_ai if mode == "ai" else run_plain)(url, rec, stop)
        except Exception as e:
            print(now_iso(), "소켓 예외", repr(e), file=sys.stderr)
        stop.set()
        rec.flush(force=True)
        rec.render()
        # ponytail: 끊기면 5초 뒤 처음부터(생중계 재확인→재접속). 회의 종료면 pick_live 가 None 을 주고 대기로 넘어간다.
        print(now_iso(), "연결 종료, 5초 후 재확인")
        time.sleep(5)


def replay(path):
    path = Path(path)
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    meta = json.loads((path.parent / "meta.json").read_text(encoding="utf-8")) if (path.parent / "meta.json").exists() else {"title": "replay"}
    outdir = path.parent / "replay"
    for f in ("transcript.jsonl", "alerts.jsonl", "raw_events.jsonl"):
        (outdir / f).unlink(missing_ok=True) if outdir.exists() else None
    t = [0.0]
    rec = Recorder(outdir, meta, clock=lambda: t[0])
    for ev in events:
        t[0] = dt.datetime.fromisoformat(ev["captured_at"]).timestamp()
        (rec.on_ai_message if ev.get("mode") == "ai" else rec.on_plain_message)(ev["raw"], ts=ev["captured_at"])
        rec.flush()
    rec.flush(force=True)
    rec.render()
    print("재생성:", outdir, "턴", len(rec.turns))


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True, errors="replace")   # 윈도우 cp949 콘솔에서 '—' 등으로 죽지 않게
    sys.stderr.reconfigure(errors="replace")
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--xcode", help="위원회 코드 (live_list.asp xcode, 산자중기위=55)")
    ap.add_argument("--any", action="store_true", help="아무 생중계나 잡는다 (실측용)")
    ap.add_argument("--replay", help="raw_events.jsonl 경로")
    ap.add_argument("--send", help="회의 폴더: transcript.txt 를 텔레그램으로 보낸다 (Actions 마무리용)")
    a = ap.parse_args()
    if a.send:
        d = Path(a.send)
        if (d / "transcript.txt").exists():
            send_telegram(f"국감 전체 스크립트 {d.name}", doc=d / "transcript.txt")
    else:
        replay(a.replay) if a.replay else record(a)
