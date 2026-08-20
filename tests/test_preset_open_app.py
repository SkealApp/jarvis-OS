"""Tests du step open_app — n'ouvre une app que si elle n'est pas déjà lancée."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from jarvis.capabilities.skills.base import PresetStep
from jarvis.capabilities.skills.executor import PresetExecutor, _is_process_running


def _chrome_step() -> PresetStep:
    return PresetStep(
        {
            "name": "Ouvrir Chrome",
            "type": "open_app",
            "process": "chrome",
            "platforms": {"windows": "chrome", "mac": "open -a 'Google Chrome'"},
            "windows_paths": [r"C:\Program Files\Google\Chrome\Application\chrome.exe"],
        }
    )


@pytest.mark.asyncio
async def test_open_app_skips_if_already_running() -> None:
    executor = PresetExecutor()
    with patch(
        "jarvis.capabilities.skills.executor._is_process_running",
        AsyncMock(return_value=True),
    ):
        result = await executor._exec_open_app(_chrome_step())
    assert result["status"] == "skipped"
    assert "déjà ouvert" in result["message"]


@pytest.mark.asyncio
async def test_open_app_launches_if_not_running() -> None:
    executor = PresetExecutor()
    with (
        patch(
            "jarvis.capabilities.skills.executor._is_process_running",
            AsyncMock(return_value=False),
        ),
        patch(
            "jarvis.capabilities.skills.executor._start_windows_app",
            AsyncMock(return_value=True),
        ),
        patch(
            "jarvis.capabilities.skills.executor._run_shell",
            AsyncMock(return_value=True),
        ),
        patch("jarvis.capabilities.skills.executor.platform.system", return_value="Windows"),
    ):
        result = await executor._exec_open_app(_chrome_step())
    assert result["status"] == "done"


@pytest.mark.asyncio
async def test_is_process_running_parses_tasklist() -> None:
    proc = AsyncMock()
    proc.communicate = AsyncMock(
        return_value=(b"chrome.exe                    1234 Console  1    80,000 K\n", b"")
    )
    proc.returncode = 0
    with (
        patch("jarvis.capabilities.skills.executor.platform.system", return_value="Windows"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        assert await _is_process_running("chrome") is True


@pytest.mark.asyncio
async def test_is_process_running_absent() -> None:
    proc = AsyncMock()
    proc.communicate = AsyncMock(
        return_value=(b"INFO: No tasks are running which match the specified criteria.\n", b"")
    )
    proc.returncode = 0
    with (
        patch("jarvis.capabilities.skills.executor.platform.system", return_value="Windows"),
        patch("asyncio.create_subprocess_exec", AsyncMock(return_value=proc)),
    ):
        assert await _is_process_running("chrome") is False
