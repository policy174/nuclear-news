"""테스트 공용 fake telegram_send — 실제 모듈은 토큰 없으면 import 시 sys.exit 하므로
모든 테스트가 이 모듈 하나를 sys.modules 에 선등록해 공유한다."""
import sys
import types

fake = types.ModuleType("telegram_send")
fake.sent_messages = []
fake.fail_next = False


def _send_long_text(text, parse_mode="HTML", reply_markup=None, disable_preview=False):
    fake.sent_messages.append({"text": text, "reply_markup": reply_markup})
    if fake.fail_next:
        fake.fail_next = False
        raise RuntimeError("telegram down (모의)")
    return [{"ok": True}]


fake.send_long_text = _send_long_text
fake.send_text = lambda *a, **k: {"ok": True}

sys.modules.setdefault("telegram_send", fake)
# 다른 fake 가 먼저 등록됐어도 항상 같은 객체를 쓰도록 노출
installed = sys.modules["telegram_send"]
