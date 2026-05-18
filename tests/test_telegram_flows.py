"""
AgriMesh V4.0 — Telegram Flow Suite (Sprint 5, Phase B / file 4)

Pins the Telegram surface area that doesn't require a live Bot API:
- the pure helpers (date parsing, expense categorisation, dashboard URLs,
  state plumbing) used by the conversation handlers
- the dispatcher wiring (commands, voice/photo/text routes, callbacks)

Anything that needs a live Telegram Application is exercised separately
in higher-level e2e tests; here we keep the unit surface tight so refactors
of the registration/follow-up flows surface immediately.
"""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest

from app.utils.time import utc_now


@pytest.fixture
def bot_app(monkeypatch):
    """Build a Telegram Application with a dummy token so create_bot succeeds."""
    from app.bot import telegram_bot as tb

    monkeypatch.setattr(tb.settings, "telegram_bot_token", "123:dummy-test-token", raising=False)
    return tb.create_bot()


# ─── _parse_sowing_date — full happy-path + edge coverage ──────────────


def test_parse_sowing_date_iso_ok():
    from app.bot.telegram_bot import _parse_sowing_date

    dt = _parse_sowing_date("2024-06-01")
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2024, 6, 1)


def test_parse_sowing_date_strips_whitespace_and_lowercases():
    from app.bot.telegram_bot import _parse_sowing_date

    assert _parse_sowing_date("  2024-01-15  ") is not None
    # 'SKIP' uppercase should still be treated as skip (lowercased internally).
    assert _parse_sowing_date("SKIP") is None


def test_parse_sowing_date_hindi_skip_variants():
    from app.bot.telegram_bot import _parse_sowing_date

    for token in ("छोड़ें", "छोडें", "skip", "-", ""):
        assert _parse_sowing_date(token) is None


def test_parse_sowing_date_rejects_european_format():
    from app.bot.telegram_bot import _parse_sowing_date

    # The parser is strict on YYYY-MM-DD to avoid month/day confusion.
    assert _parse_sowing_date("15-08-2024") is None
    assert _parse_sowing_date("15/08/2024") is None


def test_parse_sowing_date_rejects_garbage():
    from app.bot.telegram_bot import _parse_sowing_date

    assert _parse_sowing_date("hello") is None
    assert _parse_sowing_date("2024") is None
    assert _parse_sowing_date("2024-13-01") is None  # invalid month


def test_parse_sowing_date_rejects_future():
    from app.bot.telegram_bot import _parse_sowing_date

    future = (utc_now() + timedelta(days=1)).strftime("%Y-%m-%d")
    assert _parse_sowing_date(future) is None


def test_parse_sowing_date_today_accepted():
    """Today is the boundary — strictly greater-than-now is rejected."""
    from app.bot.telegram_bot import _parse_sowing_date

    today = utc_now().strftime("%Y-%m-%d")
    assert _parse_sowing_date(today) is not None


def test_parse_sowing_date_none_input_safe():
    from app.bot.telegram_bot import _parse_sowing_date

    assert _parse_sowing_date(None) is None


# ─── _state_sowing_date helper ─────────────────────────────────────────


def test_state_sowing_date_returns_now_when_missing():
    from app.bot.telegram_bot import _state_sowing_date

    dt = _state_sowing_date({})
    assert dt is not None
    # Within a generous window — utc_now() fallback.
    assert abs((utc_now().replace(tzinfo=dt.tzinfo) - dt).total_seconds()) < 5


def test_state_sowing_date_uses_stored_iso():
    from app.bot.telegram_bot import _state_sowing_date

    state = {"data": {"sowing_date": "2024-04-15T00:00:00"}}
    dt = _state_sowing_date(state)
    assert (dt.year, dt.month, dt.day) == (2024, 4, 15)


def test_state_sowing_date_invalid_iso_falls_back_to_now():
    from app.bot.telegram_bot import _state_sowing_date

    state = {"data": {"sowing_date": "not a date"}}
    dt = _state_sowing_date(state)
    # Falls back to utc_now() — should be recent.
    assert abs((utc_now().replace(tzinfo=dt.tzinfo) - dt).total_seconds()) < 5


# ─── get_user_state isolation ──────────────────────────────────────────


def test_get_user_state_initialises_with_start():
    from app.bot.telegram_bot import get_user_state

    state = get_user_state("__test_user_init__")
    assert state["state"] == "start"
    assert "data" in state


def test_get_user_state_returns_same_dict_across_calls():
    """The handler relies on identity — calls for the same id must alias."""
    from app.bot.telegram_bot import get_user_state

    a = get_user_state("__test_user_alias__")
    a["data"]["marker"] = "x"
    b = get_user_state("__test_user_alias__")
    assert b is a
    assert b["data"]["marker"] == "x"


def test_get_user_state_isolates_users():
    from app.bot.telegram_bot import get_user_state

    a = get_user_state("__test_user_isolation_a__")
    a["data"]["marker"] = "for-a"
    b = get_user_state("__test_user_isolation_b__")
    assert "marker" not in b["data"]


# ─── _guess_expense_category — bilingual mapping ───────────────────────


def test_guess_expense_seed_english_and_hindi():
    from app.bot.telegram_bot import _guess_expense_category

    assert _guess_expense_category("rice seed") == "seed"
    assert _guess_expense_category("बीज") == "seed"


def test_guess_expense_fertilizer():
    from app.bot.telegram_bot import _guess_expense_category

    for token in ("urea bag", "DAP", "NPK 19:19:19", "खाद", "यूरिया"):
        assert _guess_expense_category(token) == "fertilizer", token


def test_guess_expense_pesticide():
    from app.bot.telegram_bot import _guess_expense_category

    assert _guess_expense_category("insecticide bottle") == "pesticide"
    assert _guess_expense_category("कीटनाशक") == "pesticide"


def test_guess_expense_labour():
    from app.bot.telegram_bot import _guess_expense_category

    assert _guess_expense_category("मजदूर payment") == "labour"
    assert _guess_expense_category("labor for sowing") == "labour"


def test_guess_expense_irrigation():
    from app.bot.telegram_bot import _guess_expense_category

    assert _guess_expense_category("diesel for pump") == "irrigation"
    assert _guess_expense_category("सिंचाई") == "irrigation"


def test_guess_expense_falls_back_to_other():
    from app.bot.telegram_bot import _guess_expense_category

    assert _guess_expense_category("random thing") == "other"
    assert _guess_expense_category("") == "other"


# ─── _dashboard_url ────────────────────────────────────────────────────


def test_dashboard_url_with_farmer_id_uses_farmer_id_query():
    from app.bot.telegram_bot import _dashboard_url

    url = _dashboard_url(farmer_id="abc-123")
    assert "farmer_id=abc-123" in url
    assert "mode=farmer" in url


def test_dashboard_url_with_phone_only_uses_phone_query():
    from app.bot.telegram_bot import _dashboard_url

    url = _dashboard_url(phone="9112345678")
    assert "phone=9112345678" in url
    assert "farmer_id=" not in url


def test_dashboard_url_prefers_farmer_id_over_phone():
    from app.bot.telegram_bot import _dashboard_url

    url = _dashboard_url(farmer_id="abc", phone="9112345678")
    assert "farmer_id=abc" in url
    assert "phone=" not in url


def test_dashboard_url_without_args_returns_base_mode():
    from app.bot.telegram_bot import _dashboard_url

    url = _dashboard_url()
    assert url.endswith("?mode=farmer")


# ─── create_bot: dispatcher wiring ─────────────────────────────────────


def test_create_bot_returns_none_when_token_missing(monkeypatch):
    from app.bot import telegram_bot as tb

    monkeypatch.setattr(tb.settings, "telegram_bot_token", "", raising=False)
    assert tb.create_bot() is None


def test_create_bot_registers_text_handler(bot_app):
    from telegram.ext import MessageHandler

    from app.bot.telegram_bot import handle_text

    found = [
        h for handlers in bot_app.handlers.values() for h in handlers
        if isinstance(h, MessageHandler) and h.callback is handle_text
    ]
    assert found, "handle_text not wired"


def test_create_bot_registers_photo_handler(bot_app):
    from telegram.ext import MessageHandler

    from app.bot.telegram_bot import handle_photo

    found = [
        h for handlers in bot_app.handlers.values() for h in handlers
        if isinstance(h, MessageHandler) and h.callback is handle_photo
    ]
    assert found, "handle_photo not wired"


def test_create_bot_registers_voice_handler(bot_app):
    """Bug-8 regression also covered here — keep both checks alive."""
    from telegram.ext import MessageHandler

    from app.bot.telegram_bot import handle_voice

    found = [
        h for handlers in bot_app.handlers.values() for h in handlers
        if isinstance(h, MessageHandler) and h.callback is handle_voice
    ]
    assert found, "handle_voice not wired"


def test_create_bot_registers_callback_handler(bot_app):
    from telegram.ext import CallbackQueryHandler

    from app.bot.telegram_bot import handle_callback

    found = [
        h for handlers in bot_app.handlers.values() for h in handlers
        if isinstance(h, CallbackQueryHandler) and h.callback is handle_callback
    ]
    assert found, "handle_callback not wired"


@pytest.mark.asyncio
async def test_handle_callback_ignores_stale_callback_ack():
    from app.bot.telegram_bot import handle_callback

    class Query:
        data = "cmd_help"
        message = SimpleNamespace(reply_text=lambda *args, **kwargs: None)

        async def answer(self):
            raise BadRequest("Query is too old and response timeout expired or query id is invalid")

    replied = {}

    async def reply_text(*args, **kwargs):
        replied["called"] = True

    query = Query()
    query.message = SimpleNamespace(reply_text=reply_text)
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=123),
    )

    await handle_callback(update, SimpleNamespace())

    assert replied["called"]


@pytest.mark.parametrize(
    "command,callback_name",
    [
        ("start", "start"),
        ("register", "register"),
        ("field", "field_command"),
        ("crop", "crop_command"),
        ("memory", "memory_command"),
        ("why", "why_command"),
        ("sources", "sources_command"),
        ("feedback", "feedback_command"),
        ("outcome", "outcome_command"),
        ("dashboard", "dashboard_command"),
        ("health", "health_command"),
        ("expense", "expense_command"),
        ("sale", "sale_command"),
        ("finance", "finance_command"),
    ],
)
def test_create_bot_registers_command_handler(bot_app, command, callback_name):
    """Spot-check that every command the docs advertise is actually wired up."""
    from telegram.ext import CommandHandler

    from app.bot import telegram_bot as tb

    expected = getattr(tb, callback_name)
    found = []
    for handlers in bot_app.handlers.values():
        for h in handlers:
            if not isinstance(h, CommandHandler):
                continue
            # `gated_command` wraps non-bypass handlers; unwrap before identity check.
            cb = getattr(h.callback, "__wrapped__", h.callback)
            if cb is not expected:
                continue
            commands = h.commands  # python-telegram-bot stores commands as frozenset[str]
            if command in commands:
                found.append(h)
    assert found, f"/{command} -> {callback_name} not wired"


# ─── Sprint A4: pincode validation + registration gate ────────────────


def _make_update(user_id: str = "999111", text: str = ""):
    """Minimal Update stub that captures reply_text calls."""
    sent: list[str] = []

    async def reply_text(*args, **kwargs):
        if args:
            sent.append(args[0])

    msg = SimpleNamespace(reply_text=reply_text, text=text)
    user = SimpleNamespace(id=int(user_id), full_name="Test User", language_code="en")
    update = SimpleNamespace(
        message=msg,
        effective_user=user,
        effective_message=msg,
    )
    return update, sent


@pytest.mark.asyncio
async def test_pincode_rejects_invalid():
    from app.bot.telegram_bot import _handle_pincode_registration, get_user_state

    state = get_user_state("888001")
    state.setdefault("data", {})
    state["state"] = "registering_pincode"
    state["data"]["preferred_language"] = "en"

    update, sent = _make_update()
    await _handle_pincode_registration(update, "888001", "12abc", state)

    assert state["state"] == "registering_pincode"
    assert "pincode" not in state["data"]
    assert any("6 digits" in s.lower() or "6 digits" in s for s in sent)


@pytest.mark.asyncio
async def test_pincode_rejects_leading_zero():
    from app.bot.telegram_bot import _handle_pincode_registration, get_user_state

    state = get_user_state("888002")
    state.setdefault("data", {})
    state["state"] = "registering_pincode"
    state["data"]["preferred_language"] = "en"

    update, _ = _make_update()
    await _handle_pincode_registration(update, "888002", "012345", state)

    assert state["state"] == "registering_pincode"
    assert "pincode" not in state["data"]


@pytest.mark.asyncio
async def test_pincode_accepts_valid_and_advances():
    from app.bot.telegram_bot import _handle_pincode_registration, get_user_state

    state = get_user_state("888003")
    state.setdefault("data", {})
    state["state"] = "registering_pincode"
    state["data"]["preferred_language"] = "en"

    update, _ = _make_update()
    await _handle_pincode_registration(update, "888003", " 811201 ", state)

    assert state["data"]["pincode"] == "811201"
    assert state["state"] == "registering_tehsil"


@pytest.mark.asyncio
async def test_gate_fires_for_unregistered_user():
    from app.bot.telegram_bot import _enforce_registration_gate, get_user_state

    state = get_user_state("777001")
    state["state"] = "ready"  # pretend prior session left them here
    state.setdefault("data", {})["preferred_language"] = "en"

    update, sent = _make_update()
    gated = await _enforce_registration_gate(update, "777001", state)

    assert gated is True
    assert state["state"].startswith("registering_")
    assert any("finish registration" in s.lower() for s in sent)


@pytest.mark.asyncio
async def test_gate_passes_for_fully_registered_farmer():
    from app.bot.telegram_bot import _enforce_registration_gate, get_user_state
    from app.database import async_session_factory
    from app.models import Farmer

    user_id = "777002"
    async with async_session_factory() as db:
        db.add(Farmer(
            phone=user_id,
            hashed_password="x",
            name="Ramu",
            district="Munger",
            pincode="811201",
            tehsil="Tarapur",
            village="Asarganj",
            preferred_language="en",
        ))
        await db.commit()

    state = get_user_state(user_id)
    state["state"] = "ready"

    update, sent = _make_update(user_id)
    gated = await _enforce_registration_gate(update, user_id, state)

    assert gated is False
    assert sent == []


@pytest.mark.asyncio
async def test_gate_skips_active_registration_step():
    from app.bot.telegram_bot import _enforce_registration_gate, get_user_state

    state = get_user_state("777003")
    state["state"] = "registering_district"

    update, sent = _make_update()
    gated = await _enforce_registration_gate(update, "777003", state)

    assert gated is False
    assert sent == []


@pytest.mark.parametrize("cmd", ["/start", "/register", "/help", "/lang", "/voice_lang", "/dashboard"])
def test_gate_bypass_commands(cmd):
    from app.bot.telegram_bot import _command_is_bypassed

    assert _command_is_bypassed(cmd) is True
    assert _command_is_bypassed(f"{cmd}@AgriBot") is True


@pytest.mark.parametrize("cmd", ["/field", "/crop", "/prices", "hello", ""])
def test_gate_bypass_excludes_others(cmd):
    from app.bot.telegram_bot import _command_is_bypassed

    assert _command_is_bypassed(cmd) is False


def test_gated_command_wraps_non_bypass_handlers(bot_app):
    """`/field` and other non-bypass commands must go through `gated_command`."""
    from telegram.ext import CommandHandler

    from app.bot import telegram_bot as tb

    gated_targets = {"field", "crop", "prices", "expense", "feedback", "outcome"}
    bypass_targets = {"start", "register", "help", "dashboard"}

    for handlers in bot_app.handlers.values():
        for h in handlers:
            if not isinstance(h, CommandHandler):
                continue
            for cmd in h.commands:
                wrapped = getattr(h.callback, "__wrapped__", None)
                if cmd in gated_targets:
                    assert wrapped is not None, f"/{cmd} must be gated but isn't wrapped"
                if cmd in bypass_targets:
                    assert wrapped is None, f"/{cmd} must NOT be gated but is wrapped"


@pytest.mark.asyncio
async def test_missing_registration_field_order():
    from app.bot.telegram_bot import _missing_registration_field
    from app.models import Farmer

    # Nothing yet -> name first.
    assert _missing_registration_field(None)[0] == "name"

    # Through district, missing pincode.
    f = Farmer(phone="1", name="Ramu", district="Munger", preferred_language="en")
    assert _missing_registration_field(f)[0] == "pincode"

    # Pincode set, tehsil missing.
    f.pincode = "811201"
    assert _missing_registration_field(f)[0] == "tehsil"

    # Tehsil set, village missing.
    f.tehsil = "Tarapur"
    assert _missing_registration_field(f)[0] == "village"

    # All set -> None.
    f.village = "Asarganj"
    assert _missing_registration_field(f) is None
