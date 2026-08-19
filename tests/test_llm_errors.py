# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

from __future__ import annotations

from types import SimpleNamespace

import anthropic
from pydantic import SecretStr

from jarvis.engine.llm_errors import friendly_llm_error, llm_config_error
from jarvis.kernel.settings import Settings


def test_llm_config_error_missing_anthropic_key() -> None:
    cfg = Settings(
        user_firstname="Test",
        api_backend="anthropic",
        anthropic_api_key=SecretStr(""),
        anthropic_auth_token=SecretStr(""),
    )
    msg = llm_config_error(cfg)
    assert msg is not None
    assert "ANTHROPIC_API_KEY" in msg


def test_llm_config_error_ok_when_auth_token_present() -> None:
    cfg = Settings(
        user_firstname="Test",
        api_backend="anthropic",
        anthropic_api_key=SecretStr(""),
        anthropic_auth_token=SecretStr("sk-proxy-" + "x" * 40),
        anthropic_base_url="https://api.synterolink.com",
    )
    assert llm_config_error(cfg) is None


def test_anthropic_sdk_kwargs_uses_auth_token_and_base_url() -> None:
    cfg = Settings(
        anthropic_api_key=SecretStr(""),
        anthropic_auth_token=SecretStr("sk-proxy-token-1234567890abcdef"),
        anthropic_base_url="https://api.synterolink.com/",
    )
    kwargs = cfg.anthropic_sdk_kwargs()
    assert kwargs["auth_token"] == "sk-proxy-token-1234567890abcdef"
    assert kwargs["api_key"] == "sk-proxy-token-1234567890abcdef"
    assert kwargs["base_url"] == "https://api.synterolink.com"
    headers = cfg.anthropic_http_headers()
    assert headers["Authorization"] == "Bearer sk-proxy-token-1234567890abcdef"
    assert cfg.anthropic_models_url() == "https://api.synterolink.com/v1/models"


def _fake_response(status_code: int) -> SimpleNamespace:
    return SimpleNamespace(
        request=SimpleNamespace(),
        status_code=status_code,
        headers={},
    )


def test_friendly_llm_error_auth() -> None:
    cfg = Settings(user_firstname="Max", anthropic_api_key=SecretStr("x" * 30))
    exc = anthropic.AuthenticationError("bad key", response=_fake_response(401), body=None)
    msg = friendly_llm_error(exc, cfg)
    assert "ANTHROPIC_API_KEY" in msg
    assert "Max" in msg


def test_friendly_llm_error_rate_limit() -> None:
    cfg = Settings(user_firstname="Max", anthropic_api_key=SecretStr("x" * 30))
    exc = anthropic.RateLimitError("slow down", response=_fake_response(429), body=None)
    msg = friendly_llm_error(exc, cfg)
    assert "débit" in msg.lower() or "Réessaie" in msg


def test_friendly_llm_error_generic_fallback() -> None:
    cfg = Settings(user_firstname="Max", anthropic_api_key=SecretStr("x" * 30))
    msg = friendly_llm_error(RuntimeError("unexpected"), cfg)
    assert "souci" in msg
