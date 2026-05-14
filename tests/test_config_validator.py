from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

VALID_PROD = {
    "app_env": "production",
    "api_key": "a" * 32,
    "allowed_origins": ["https://agrimesh.example"],
    "database_url": "postgresql+asyncpg://user:pass@localhost/agrimesh",
    "telegram_bot_token": "123456789:valid-telegram-token",
    "enable_demo_sessions": False,
    "enable_dev_farmer_header": False,
    "demo_allowed_farmers": [],
    "demo_allowed_workers": [],
    "bot_demo_secret_current": "",
    "bot_demo_secret_previous": "",
}


@pytest.mark.parametrize(
    "override",
    [
        {"api_key": ""},
        {"api_key": "short"},
        {"api_key": "placeholder"},
        {"allowed_origins": ["*"]},
        {"database_url": "sqlite+aiosqlite:///./data/prod.db"},
        {"telegram_bot_token": ""},
        {"telegram_bot_token": "placeholder"},
        {"enable_demo_sessions": True},
        {"enable_dev_farmer_header": True},
        {"demo_allowed_farmers": ["farmer_demo_munger_001"]},
        {"demo_allowed_workers": ["worker_demo_extension_001"]},
        {"bot_demo_secret_current": "x" * 32},
        {"bot_demo_secret_previous": "y" * 32},
    ],
)
def test_invalid_production_settings_fail_fast(override):
    payload = VALID_PROD | override

    with pytest.raises(ValidationError):
        Settings(**payload)


def test_production_defaults_are_not_valid():
    with pytest.raises(ValidationError):
        Settings(app_env="production")


def test_valid_production_settings():
    settings = Settings(**VALID_PROD)

    assert settings.is_production is True
    assert settings.api_key_required is True


def test_valid_demo_settings_with_enabled_sessions():
    settings = Settings(
        app_env="demo",
        enable_demo_sessions=True,
        bot_demo_secret_current="z" * 32,
        demo_allowed_farmers=["farmer_demo_munger_001"],
        demo_allowed_workers=["worker_demo_extension_001"],
    )

    assert settings.app_env == "demo"
    assert settings.enable_demo_sessions is True


def test_demo_enabled_requires_strong_current_secret():
    with pytest.raises(ValidationError):
        Settings(app_env="demo", enable_demo_sessions=True, bot_demo_secret_current="short")
