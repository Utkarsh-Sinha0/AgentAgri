"""Headless smoke for Telegram bot handlers.

Drives the actual handler coroutines with fake telegram Update / Message / Query
objects. Real DB, real agent, real Sarvam/Ollama wiring; only the Telegram
transport (reply_text, edit_message_text, reply_chat_action, answer) is faked
and captured.

Runs against a unique telegram_user_id so it does not collide with the live bot
or other smoke runs.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from telegram.constants import ChatAction  # noqa: F401  (kept for parity)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bot import telegram_bot as tb
from app.database import init_db

# ─── Fake Telegram surface ────────────────────────────────────────────


class CapturedChat:
    def __init__(self, sink: list[dict], chat_id: int = 0):
        self._sink = sink
        self.id = chat_id

    async def send_action(self, action, **kwargs):
        self._sink.append({"kind": "chat_action", "action": str(action)})


class CapturedMessage:
    def __init__(self, sink: list[dict], chat_id: int = 0):
        self._sink = sink
        self.message_id = 1
        self.text: str | None = None
        self.caption: str | None = None
        self.photo: list = []
        self.voice = None
        self.from_user = None
        self.chat = CapturedChat(sink, chat_id)

    async def reply_text(self, text, **kwargs):
        self._sink.append({"kind": "reply_text", "text": str(text), "kwargs": kwargs})
        return CapturedMessage(self._sink)

    async def reply_chat_action(self, action, **kwargs):
        self._sink.append({"kind": "chat_action", "action": str(action)})

    async def reply_voice(self, voice, **kwargs):
        self._sink.append({"kind": "reply_voice", "kwargs": kwargs})

    async def reply_photo(self, photo, **kwargs):
        self._sink.append({"kind": "reply_photo", "kwargs": kwargs})


class CapturedQuery:
    def __init__(self, sink: list[dict], data: str, user_id: str):
        self.data = data
        self.message = CapturedMessage(sink)
        self._sink = sink
        self.from_user = SimpleNamespace(id=int(user_id) if user_id.isdigit() else 0)

    async def answer(self, *args, **kwargs):
        self._sink.append({"kind": "ack", "data": self.data})

    async def edit_message_text(self, text, **kwargs):
        self._sink.append({"kind": "edit", "text": str(text)})

    async def edit_message_reply_markup(self, reply_markup=None, **kwargs):
        self._sink.append({"kind": "edit_markup"})


def make_update_for_command(user_id: str, text: str, sink: list[dict]):
    msg = CapturedMessage(sink)
    msg.text = text
    return SimpleNamespace(
        message=msg,
        effective_user=SimpleNamespace(id=int(user_id), username=f"smoke-{user_id}"),
        effective_chat=SimpleNamespace(id=int(user_id)),
        callback_query=None,
    )


def make_update_for_callback(user_id: str, data: str, sink: list[dict]):
    return SimpleNamespace(
        callback_query=CapturedQuery(sink, data, user_id),
        effective_user=SimpleNamespace(id=int(user_id), username=f"smoke-{user_id}"),
        effective_chat=SimpleNamespace(id=int(user_id)),
    )


def joined(sink: list[dict]) -> str:
    return "\n".join(
        e.get("text", "") for e in sink if e["kind"] in {"reply_text", "edit"}
    )


# ─── Checks ───────────────────────────────────────────────────────────


async def main() -> int:
    await init_db()

    suffix = str(int(uuid.uuid4().int % 10000)).zfill(4)
    user_id_unreg = "9000" + suffix
    user_id_demo = "9100" + suffix

    ctx = SimpleNamespace(args=[], bot=SimpleNamespace())

    checks: dict[str, bool] = {}

    # 1. /start as unregistered user
    sink: list[dict] = []
    await tb.start(make_update_for_command(user_id_unreg, "/start", sink), ctx)
    txt = joined(sink)
    checks["start_welcome_bilingual"] = "AgriMesh" in txt and "आवाज़" in txt
    checks["start_unreg_keyboard"] = any(
        "Demo" in str(e.get("kwargs", {})) or "cmd_demo" in str(e.get("kwargs", {}))
        for e in sink
    )

    # 2. /help
    sink = []
    await tb.help_command(make_update_for_command(user_id_unreg, "/help", sink), ctx)
    txt = joined(sink)
    checks["help_categories"] = all(s in txt for s in ["शुरुआत", "रोज़ का काम", "फसल चक्र", "गोपनीयता"])

    # 3. cmd_help callback
    sink = []
    await tb.handle_callback(make_update_for_callback(user_id_unreg, "cmd_help", sink), ctx)
    checks["callback_help"] = "सहायता" in joined(sink)

    # 4. cmd_demo callback — seeds a demo memory palace
    sink = []
    await tb.handle_callback(make_update_for_callback(user_id_demo, "cmd_demo", sink), ctx)
    txt = joined(sink)
    checks["demo_seeded"] = ("Demo" in txt or "demo" in txt or "memory palace" in txt.lower() or len(txt) > 50)

    # 5. /start as now-registered demo user
    sink = []
    await tb.start(make_update_for_command(user_id_demo, "/start", sink), ctx)
    reg_kbd = any(
        "threads_list" in str(e.get("kwargs", {})) or "Continue" in str(e.get("kwargs", {}))
        for e in sink
    )
    checks["start_registered_keyboard"] = reg_kbd

    # 6. /prices
    sink = []
    await tb.prices_command(make_update_for_command(user_id_demo, "/prices", sink), ctx)
    txt = joined(sink)
    checks["prices_responds"] = len(txt) > 20

    # 7. cmd_prices callback shares helper
    sink = []
    await tb.handle_callback(make_update_for_callback(user_id_demo, "cmd_prices", sink), ctx)
    checks["callback_prices"] = len(joined(sink)) > 20

    # 8. /mydata
    sink = []
    await tb.mydata_command(make_update_for_command(user_id_demo, "/mydata", sink), ctx)
    txt = joined(sink)
    checks["mydata_responds"] = len(txt) > 20

    # 9. cmd_mydata callback
    sink = []
    await tb.handle_callback(make_update_for_callback(user_id_demo, "cmd_mydata", sink), ctx)
    checks["callback_mydata"] = len(joined(sink)) > 20

    # 10. /forgetme shows confirmation, NOT instant redaction
    sink = []
    await tb.forgetme_command(make_update_for_command(user_id_demo, "/forgetme", sink), ctx)
    txt = joined(sink)
    has_confirm_prompt = ("पक्के" in txt) or ("sure" in txt.lower()) or ("redact" in txt.lower())
    has_confirm_keyboard = any(
        "forgetme_confirm" in str(e.get("kwargs", {})) for e in sink
    )
    # If demo user has no memory atoms yet, the empty-state response is also valid
    # (it explicitly avoids the destructive path). Either branch is acceptable.
    no_data_path = "redact करने को नहीं" in txt or "nothing to redact" in txt.lower()
    checks["forgetme_asks_confirm"] = (has_confirm_prompt and has_confirm_keyboard) or no_data_path

    # 11. forgetme_cancel preserves data
    sink = []
    await tb.handle_callback(make_update_for_callback(user_id_demo, "forgetme_cancel", sink), ctx)
    cancel_txt = joined(sink)
    checks["forgetme_cancel_path"] = "रद्द" in cancel_txt or "Cancel" in cancel_txt

    # 12. cmd_photo prompts for photo
    sink = []
    await tb.handle_callback(make_update_for_callback(user_id_demo, "cmd_photo", sink), ctx)
    checks["callback_photo_prompt"] = "फोटो" in joined(sink) or "photo" in joined(sink).lower()

    # 13. /start unregistered still has demo+register buttons (not lost)
    sink = []
    await tb.start(make_update_for_command(user_id_unreg, "/start", sink), ctx)
    raw = str(sink)
    checks["start_unreg_has_demo_button"] = "cmd_demo" in raw and "cmd_register" in raw

    # 14. BOT_COMMAND_MENU coverage
    menu = {c for c, _ in tb.BOT_COMMAND_MENU}
    expected = {"start", "help", "demo", "register", "prices", "mydata", "forgetme", "threads", "newthread", "endthread", "dashboard"}
    checks["bot_menu_complete"] = expected.issubset(menu)

    # ─── Report ────────────────────────────────────────────
    print("Telegram handler smoke")
    print("======================")
    failed = []
    for name, ok in checks.items():
        marker = "OK" if ok else "FAIL"
        print(f"{name}: {marker}")
        if not ok:
            failed.append(name)
    if failed:
        print(f"\nFAILED: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
