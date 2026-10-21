"""질의 하나가 끝날 때마다(다음 위원 호명) 지니 약식 요약을 원자력 뉴스 채널에 올린다.

  python live_post.py                       # 오늘 회의 폴더 전부 감시, 60초마다
  python live_post.py --folder <폴더> --dry  # 시험: 끝난 질의 전부 출력만
  python live_post.py --folder <폴더> --to ops --limit 2   # 시험: 개인 대화로 2건만

요약은 draft.py 의 summarize 를 그대로 쓴다 — 캐시(draft_cache/)를 같이 써서 정회 때 초안이 다시 요약하지 않는다.
보낸 질의는 <폴더>/live_posted.json 에 남겨 재시작해도 두 번 안 보낸다. 실패한 질의는 다음 바퀴에 다시 시도.
"""
from summary_guard import guard_storage_titles
import argparse, datetime as dt, html, json, os, re, sys, time
from pathlib import Path

import requests

import threading
from draft import ENERGY, PARTIES, as_text, fix_name, is_cand, load_lines, load_meta, mark_unverified, review, summarize, turns
from recorder import load_env

HERE = Path(__file__).resolve().parent
ROOT = HERE / "2026-국정감사"
SHORT = {"산자중기위": "산중위", "기후노동위": "기노위", "과방위": "과방위"}
FAILS: dict = {}   # key → 연속 실패 횟수. 2회면 개인 DM 경보(10-13 최혁진·조경태 3시간 무음 실패)
WATCH = HERE / "watch_topics.json"   # 우리 처 예상질의 — 걸리면 ⭐ + 자막 원문 + 개인 대화에도(지니 10-12 "무조건 확실히")


def watch_hits(turn):
    """질의 자막 원문에 예상질의 정규식이 걸리면 [(label, [원문 문장...])]. 요약이 빠뜨려도 원문은 남긴다."""
    if not WATCH.exists():
        return []
    hits = []
    for tp in json.loads(WATCH.read_text(encoding="utf-8")).get("topics", []):
        if tp.get("member"):   # 중점 의원(10-13 지니 "오늘 이진숙 의원 질의 중요") — 주제 무관하게 전부 ⭐ + 개인 DM
            if turn.get("who") == tp["member"]:
                hits.append((tp["label"], [f"[{ts[:5]}] {t.lstrip('- ').strip()[:220]}" for ts, t in turn["lines"][1:4]]))
            continue
        rx = re.compile(tp["pattern"])
        quotes = []
        for ts, t in turn["lines"]:
            t = t.replace(" -", " / ").lstrip("- ").strip()
            if rx.search(t) and t not in quotes:
                quotes.append(f"[{ts[:5]}] {t[:220]}")
        if quotes:
            hits.append((tp["label"], quotes[:3]))
    return hits
KHNP = re.compile(r"한\s*수\s*원|수력\s*원자력|김\s*회\s*천")
# 한수원 업무보고 자기소개 문장은 뺀다 — 의원 발언 속 언급만 원한다
KHNP_SELF = re.compile(r"한수원은|한수원 전 임직원|한수원과 협력|한국수력원자력(의|기관장| 사장 나오| 업무)|이상으로 한국수력|사장 나오셔서")


def khnp_quotes(turn):
    """본질의가 아니어도 한수원 언급은 보낸다(10-19 지니: 의사진행발언 속 '한수원 자료 미제출'을 놓침). 걸린 줄+다음 줄."""
    ls, out = turn["lines"], []
    for i, (ts, t) in enumerate(ls):
        if KHNP.search(t) and not KHNP_SELF.search(t):
            q = " ".join(x.lstrip("- ").strip() for _, x in ls[max(0, i - 1):i + 2])
            out.append(f"[{ts[:5]}] {q[:300]}")
    return out[:6]
IDLE_CLOSE = 300   # 마지막 질의도 5분간 새 자막이 없으면(정회·산회) 끝난 것으로 본다


CEO = re.compile(r"한수원\s*사장|수력원자력\s*사장|김회천|김\s*회\s*천")   # 사장님 직접 질의·답변 표기(지니 10-12). 현 사장 김회천(황주호 아님)
JUDGE = re.compile(r"논란|의혹|특혜|부당|강행|전가|빌미|실현\s*가능성|불투명|압박|매몰|적법성|불일치|납득|부실|졸속|독촉|전락|우려|의도적|고의")   # 자막에 없으면 내 해석 — 쓰지 않는다


def ceo_hit(turn, res):
    """사장이 실제로 답변한 질의만: 요약 답변자가 한수원사장이거나, (에너지 질의이면서) 자막에 사장 호칭이 있을 때.
    단순 언급("한수원 사장님도 계시기 때문에")만으로 빈 요약을 보낸 사고(10-12 서지영) 방지."""
    if any("한수원" in l.get("who", "") and "사장" in l.get("who", "") for b in res.get("blocks") or [] for l in b.get("lines") or []):
        return True
    return bool(res.get("energy")) and any(CEO.search(t) for _, t in turn["lines"])


def fmt(cmte, who, res, hits=(), ceo=False, start=None):
    """summarize 결과 → (산중위-OOO 의원) ▶ 주제 / Q·A. hits 가 있으면 ⭐ 머리말 + 예상질의 라벨 + 자막 원문."""
    res = guard_storage_titles(res)
    blocks = [mark_unverified(dict(b, lines=[dict(l) for l in b["lines"]])) for b in res.get("blocks") or [] if b.get("lines")]   # 미확인 수치엔 [?]
    for b in blocks:   # 캐시된 옛 요약의 비문 보정(10-12 지니: "그렇음? 이게 쓰는 말이야?")
        for l in b["lines"]:
            l["text"] = re.sub(r"^(?:예,?\s*)?그렇음\.?$", "그렇다고 답함.", l["text"].strip())
    # 호명 오인식(10-06 '박재호' 호명 뒤 실제 박수민) → 요약에 가장 많이 나온 의원 이름을 쓴다
    names = [l["who"] for b in blocks for l in b["lines"] if not re.search(r"장관|차관|사장|위원장|실장|청장|원장|대표|국장", l["who"])]
    who = max(set(names), key=names.count) if names else who
    parties = json.loads(PARTIES.read_text(encoding="utf-8")) if PARTIES.exists() else {}
    E = html.escape
    star = "⭐ " if hits else ""
    ABBR = {"더불어민주당": "민주", "국민의힘": "국힘", "조국혁신당": "조국", "진보당": "진보", "무소속": "무소속"}
    if blocks or not start:   # 머리말만 정당 약어 포함 (과방위-김선민-조국) — 10-13 지니. 명단에 없으면 (과방위-OOO 의원)
        name = fix_name(who, parties); ab = ABBR.get(parties.get(name, ""), "")
        if name not in parties:   # 10-13 '이은구' — 명단 밖 이름은 오인식일 수 있다고 표시
            name += "(이름 확인 필요)"
        out = [f"<b>{star}({cmte}-{name}-{ab})</b>" if ab else f"<b>{star}({cmte}-{name} 의원)</b>"]
    else:   # 요약 없이 ⭐ 원문만 나가는 글엔 의원 이름을 달지 않는다 — 턴 경계가 틀렸을 때 엉뚱한 이름이 붙음(10-13 이연희/서천호)
        out = [f"<b>{star}({cmte} {start} 구간, 발언자 확인 중)</b>"]
    if ceo:
        out.append("<b>■ 한수원 사장 직접 질의·답변</b>")
    # 채널은 공유 공간 — '예상질의' 라벨은 쓰지 않고 ⭐ 표시와 원문 인용만(지니 10-12)
    titles = [re.sub(r"[「」\"']", "", b.get("title", "")).strip() for b in blocks]
    if any(titles):
        out.append("▶ " + E(" · ".join(t for t in titles if t)))   # 폰 알림 미리보기용 한 줄
    for b, title in zip(blocks, titles):
        if title and len(blocks) > 1:
            out.append(f"\n<b>[{E(title)}]</b>")
        ls = b["lines"]
        # 실제 순서 그대로. 의원 줄 뒤에 답변이 오면 Qn/An, 답변 없이 끝나면 ㅇ. 같은 사람 연속 줄은 합친다.
        merged = []
        for l in ls:
            if merged and merged[-1]["who"] == l["who"]:
                merged[-1]["text"] += " " + l["text"]
            else:
                merged.append(dict(l))
        n = 0
        for i, l in enumerate(merged):
            mine = l["who"] == who
            nxt = merged[i + 1] if i + 1 < len(merged) else None
            if mine and nxt:
                n += 1
                out.append(f"\n<b>Q{n}.</b> {E(l['text'])}")
            elif mine:
                out.append(f"\nㅇ {E(l['text'])}")
            else:
                ans = re.sub(r"([^.]*?하겠음\.)", r"<b>\1</b>", E(l["text"]))   # 약속("~하겠음") 문장만 굵게
                out.append(f"<b>A{n}.</b> ({E(l['who'])}) {ans}" if n else f"\nㅇ ({E(l['who'])}) {ans}")
        if b.get("unverified"):
            out.append(f"\n<i>※ 자막 대조 필요: {E(', '.join(b['unverified']))}</i>")
    if hits and not blocks:   # 요약이 없을 때만 원문 인용(10-19 지니 "자막 원문은 안 줘도 돼") — 있으면 빈 글 방지용
        out.append("\n<b>⭐ 자막 원문</b>")
        seen = []
        for _, quotes in hits:
            seen += [q for q in quotes if q not in seen]
        out += [f"<i>{E(q)}</i>" for q in seen]
    return "\n".join(out)


def send(text, to):
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_SUMMARY_CHAT_ID") if to == "summary" else os.environ.get("TELEGRAM_OPS_CHAT_ID")
    if not (tok and chat):
        raise RuntimeError("텔레그램 토큰·대화방 없음")
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML"}, timeout=15)
    if not (r.ok and r.json().get("ok")):
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:150]}")
    return chat, r.json()["result"]["message_id"]


def edit(ref, text):
    """이미 올린 글을 제자리에서 고친다 — 수정본을 새 글로 올리면 채널이 시끄럽다(10-12 지니 "수정이 너무 자주 오는데")."""
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat, mid = ref
    r = requests.post(f"https://api.telegram.org/bot{tok}/editMessageText",
                      json={"chat_id": chat, "message_id": mid, "text": text[:4000], "parse_mode": "HTML"}, timeout=15)
    if not (r.ok and r.json().get("ok")) and "not modified" not in r.text:
        raise RuntimeError(f"edit HTTP {r.status_code} {r.text[:150]}")


def send_script(turn, cmte, who, date, to):
    """요약 바로 뒤에 그 질의의 자막 원문 txt(지니 10-12 '요약 다음에 바로 실시간 스크립트'). 영문 파일명(회사 필터)."""
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_SUMMARY_CHAT_ID") if to == "summary" else os.environ.get("TELEGRAM_OPS_CHAT_ID")
    body = f"{date} {cmte} {who} 의원 질의 자막 원문 ({turn['a'][:5]}~{turn['b'][:5]}) — 국회 AI 자막, 미가공\n\n" + \
        "\n".join(f"[{ts[:5]}] {t.replace(' -', ' / ').lstrip('- ').strip()}" for ts, t in turn["lines"])
    name = f"script_{date.replace('-', '')}_{turn['a'][:5].replace(':', '')}.txt"
    r = requests.post(f"https://api.telegram.org/bot{tok}/sendDocument", data={"chat_id": chat, "caption": f"📄 {who} 의원 질의 스크립트({turn['a'][:5]}~{turn['b'][:5]})"},
                      files={"document": (name, body.encode("utf-8"), "text/plain")}, timeout=60)
    if not (r.ok and r.json().get("ok")):
        raise RuntimeError(f"스크립트 HTTP {r.status_code} {r.text[:150]}")


def post_review(t, res, meta, lines, cache, cmte, to, hits, ceo, refs):
    """발송 뒤 백그라운드 검수. 확실한 오류(평가어·화자 뒤바뀜·한수원 누락)만 재요약해 **올린 글을 제자리에서 고친다**. 새 글 안 올림."""
    try:
        raw = re.sub(r"\s", "", " ".join(x for _, x in t["lines"]))
        words = sorted({w for b in res.get("blocks") or [] for w in JUDGE.findall(b.get("title", "") + " " + " ".join(l["text"] for l in b["lines"])) if re.sub(r"\s", "", w) not in raw})
        probs = [f"5: 자막에 없는 평가어 → 빼거나 의원이 실제 쓴 표현으로: {', '.join(words)}"] if words else []
        fb = review(t, as_text(t["lines"]), res)
        probs += [p for p in fb.splitlines() if p.startswith("- ") and p[2:3] in "236"]   # 모델 검수는 화자(2)·한수원 누락(3)만 — 논점·문장 지적은 취향이라 안 고침
        if not probs:
            return
        res2 = summarize(t, meta, lines, cache, feedback="\n검수 지적 — 아래를 고쳐서 다시 써라:\n- " + "\n- ".join(p.lstrip("- ") for p in probs) + "\n")
        if not res2.get("blocks"):
            return
        text = fmt(cmte, t["who"], res2, hits, ceo) + "\n<i>(검수 후 수정)</i>"
        for ref in refs:
            edit(ref, text)
        print(f"{t['a']}_{t['who']} 제자리 수정: {probs[0][:60]}", flush=True)
    except Exception as e:
        print(f"{t['a']}_{t['who']} 검수 오류: {str(e)[:120]}", flush=True)


def alert_fail(key, msg):
    FAILS[key] = FAILS.get(key, 0) + 1
    if FAILS[key] == 2:
        try:
            send(f"⚠ 실시간 발송기: {key} 2회 연속 {msg}", "ops")
        except Exception:
            pass


def closed_turns(folder: Path):
    lines = load_lines(folder, None)
    ts = turns(lines)
    if not ts:
        return lines, []
    last_age = (dt.datetime.now() - dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(lines[-1][0]))).total_seconds()
    done = ts[:-1] + (ts[-1:] if last_age > IDLE_CLOSE or folder.name[:10] != dt.date.today().isoformat() else [])
    return lines, [t for t in done if is_cand(t) or (t["who"] and watch_hits(t)) or khnp_quotes(t)]


ORDER = HERE / "order_today.json"   # 그날 질의 순서표(지니가 보내 준 사진) — 호명이 순서를 건너뛰면 DM(10-13 "왜 자꾸 실수하지")


def order_check(folder: Path, lines, cmte):
    if not ORDER.exists():
        return
    o = json.loads(ORDER.read_text(encoding="utf-8"))
    if o.get("date") != folder.name[:10] or o.get("cmte") != cmte:
        return
    order = o["order"]; idx = {n: i for i, n in enumerate(order)}
    seen = [t["who"] for t in turns(lines) if t["who"] in idx]
    state_f = folder / "order_alerts.json"
    alerted = set(json.loads(state_f.read_text(encoding="utf-8"))) if state_f.exists() else set()
    missing = []
    for a, b in zip(seen, seen[1:]):
        for k in range(idx[a] + 1, idx[b]):
            if order[k] not in seen and order[k] not in alerted:
                missing.append(order[k])
    if missing:
        try:
            send(f"<b>호명 누락 의심</b> 순서표상 {', '.join(missing)} 의원 차례였는데 호명이 안 잡힘 → 앞 의원 글에 묻혔을 수 있음. 확인 중.", "ops")
        except Exception as e:
            print(f"순서 경고 실패: {e}", flush=True)
        alerted |= set(missing)
        state_f.write_text(json.dumps(sorted(alerted), ensure_ascii=False), encoding="utf-8")
        print("순서 누락 의심:", missing, flush=True)


def run_folder(folder: Path, to: str, dry: bool, limit: int | None):
    state_f = folder / "live_posted.json"
    posted = set(json.loads(state_f.read_text(encoding="utf-8"))) if state_f.exists() and not dry else set()
    meta, cmte = load_meta(folder), SHORT.get(folder.name.split("_", 1)[1], folder.name.split("_", 1)[1])
    lines, cands = closed_turns(folder)
    if not dry:
        order_check(folder, lines, cmte)
    cache = folder / "draft_cache"
    cache.mkdir(exist_ok=True)
    sent = 0
    for t in cands:
        key = f"{t['a']}_{t['who']}"
        if key in posted:
            continue
        kq = khnp_quotes(t)

        def post_khnp():   # 요약이 안 나가는 발언 속 한수원 언급 → 원문만 (채널 + 개인 대화)
            if dry:
                print(f"[dry] {key} 한수원 언급:", kq, flush=True)
                return True
            parties = json.loads(PARTIES.read_text(encoding="utf-8")) if PARTIES.exists() else {}
            head = f"({cmte}-{fix_name(t['who'], parties)} 의원, {t['a'][:5]})" if t["who"] else f"({cmte} {t['a'][:5]}~{t['b'][:5]} 구간, 발언자 확인 중)"
            text = (f"<b>{html.escape(head)} 한수원 언급</b>\n▶ 본질의 외 발언(의사진행·자료요구 등) 중 한수원 언급\n\n"
                    + "\n".join(f"<i>{html.escape(q)}</i>" for q in kq))
            try:
                send(text, to)
                if to != "ops":
                    send(text, "ops")
            except Exception as e:
                print(f"{key} 한수원 언급 발송 실패: {e}", flush=True)
                alert_fail(key, f"한수원 언급 발송 실패: {str(e)[:150]}")
                return False
            print(f"{key} 한수원 언급 발송", flush=True)
            return True

        if not t["who"] or not (is_cand(t) or watch_hits(t)):   # 호명 밖 구간·비후보 발언은 요약 없이 한수원 언급만
            if kq and not post_khnp():
                continue
            posted.add(key)
            if not dry:
                state_f.write_text(json.dumps(sorted(posted), ensure_ascii=False), encoding="utf-8")
            continue
        try:
            res = summarize(t, meta, lines, cache)
        except Exception as e:   # 한 질의 실패가 뒤 질의를 막으면 안 된다(10-13 최혁진 실패로 6건 2시간 지연) → 건너뛰고 다음 바퀴에 다시
            print(f"{key} 요약 실패: {str(e)[:120]}", flush=True)
            alert_fail(key, f"요약 실패: {str(e)[:150]}")
            continue
        hits = watch_hits(t)
        ceo = ceo_hit(t, res)
        has_body = bool(res.get("blocks")) or bool(hits)   # 요약도 ⭐ 원문 인용도 없으면 빈 글 — 보내지 않는다
        if (res.get("energy") or hits or ceo) and has_body:   # 예상질의·사장 직접 질의는 에너지 판정과 무관하게 보낸다
            text = fmt(cmte, t["who"], res, hits, ceo, start=t["a"][:5])
            also_ops = bool(hits) or ceo   # 사장 직접 질의도 개인 대화에 한 부 더 (hits 를 True 로 덮으면 아래 all(...) 이 터진다 — 10-13 최혁진·조경태 발송 실패)
            if dry:
                print(text, "\n", flush=True)
            else:
                try:
                    m0 = re.search(r"\([^-()]+-([^-()]+)", re.sub(r"<[^>]+>", "", text.splitlines()[0]))   # 머리말 '(과방위-이름-정당)' 에서 이름
                    shown = m0.group(1).strip() if m0 else t["who"]
                    member_only = bool(hits) and all(h[0].startswith("중점") for h in hits) and not res.get("energy") and not ceo
                    dest = "ops" if member_only else to   # 중점 의원의 비에너지 질의는 채널 대신 개인 DM 만(채널 소음 방지)
                    refs = [send(text, dest)]
                    send_script(t, cmte, shown, meta["date"], dest)
                    if also_ops and dest != "ops":
                        refs.append(send(text, "ops"))   # ⭐ 는 개인 대화에도 — 채널 알림을 놓쳐도 받게
                        send_script(t, cmte, shown, meta["date"], "ops")
                    if os.environ.get("LIVE_REVIEW", "1") != "0":   # 검수는 claude 호출을 2배로 늘림 — 적체 시 LIVE_REVIEW=0 (10-13 600초 타임아웃)
                        threading.Thread(target=post_review, args=(t, res, meta, lines, cache, cmte, to, also_ops, ceo, refs), daemon=False).start()
                except Exception as e:
                    print(f"{key} 발송 실패: {e}", flush=True)
                    alert_fail(key, f"발송 실패: {str(e)[:150]}")
                    continue
                print(f"{key} 발송", flush=True)
            sent += 1
        elif kq and not post_khnp():   # 비에너지 판정이라 요약이 안 나가도 한수원 언급은(10-19)
            continue
        posted.add(key)
        if not dry:
            state_f.write_text(json.dumps(sorted(posted), ensure_ascii=False), encoding="utf-8")
        if limit and sent >= limit:
            break
    return sent


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--folder")
    ap.add_argument("--to", choices=["summary", "ops"], default="summary")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    load_env()
    if a.folder:
        run_folder(Path(a.folder), a.to, a.dry, a.limit)
        sys.exit()
    while True:   # ponytail: 60초마다 raw 전체를 다시 읽음(하루 수만 줄, 수 초). 느려지면 읽은 위치 기억
        for f in sorted(ROOT.glob(f"{dt.date.today().isoformat()}_*")):
            if (f / "raw_events.jsonl").exists():
                try:
                    run_folder(f, a.to, False, None)
                except Exception as e:
                    print(f"{f.name} 오류: {e}", flush=True)
        time.sleep(60)

