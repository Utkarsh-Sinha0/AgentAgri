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

import pytest

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
            if not isinstance(h, CommandHandler) or h.callback is not expected:
                continue
            commands = h.commands  # python-telegram-bot stores commands as frozenset[str]
            if command in commands:
                found.append(h)
    assert found, f"/{command} -> {callback_name} not wired"
