# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""Exécuteur de presets Jarvis — gère l'exécution step par step."""

from __future__ import annotations

import asyncio
import os
import platform
from pathlib import Path

from loguru import logger

from jarvis.capabilities.skills.app_checker import check_all_apps
from jarvis.capabilities.skills.base import PresetSkill, PresetStep
from jarvis.kernel.error_collector import collector  # jrv: autofix
from jarvis.kernel.notifications import broadcast_audio
from jarvis.kernel.settings import settings


class PresetExecutor:
    """
    Exécute un preset step par step.
    Types supportés : cli, open_app, spotify, tts, ai, wait, notify
    """

    def __init__(
        self, tool_registry: object = None, tts_engine: object = None, llm_client: object = None
    ) -> None:
        self._tools = tool_registry
        self._tts = tts_engine
        self._llm = llm_client

    async def execute(self, preset: PresetSkill, broadcast_fn: object = None) -> dict:
        """
        Exécute tous les steps d'un preset.
        broadcast_fn : coroutine async(dict) pour envoyer des events WebSocket.
        """

        requires_apps = preset.metadata.get("requires_apps", [])
        if requires_apps:
            apps_status = check_all_apps(requires_apps)
            missing_required = [
                a["name"] for a in apps_status["apps"] if not a["installed"] and a["required"]
            ]
            if missing_required:
                missing_str = ", ".join(missing_required)
                return {
                    "success": False,
                    "error": f"Applications requises non installées : {missing_str}",
                    "steps_done": 0,
                    "steps_skipped": 0,
                    "steps_failed": 0,
                    "logs": [],
                }

        steps = preset.get_steps()
        results = {
            "preset": preset.name,
            "success": True,
            "steps_done": 0,
            "steps_skipped": 0,
            "steps_failed": 0,
            "logs": [],
        }

        logger.info(f"Preset '{preset.name}' — démarrage ({len(steps)} steps)")

        if broadcast_fn:
            await broadcast_fn(
                {
                    "type": "preset_started",
                    "preset": preset.name,
                    "label": preset.label,
                    "total_steps": len(steps),
                }
            )

        for i, step in enumerate(steps):
            step_result = await self._execute_step(step, i + 1, len(steps))

            results["logs"].append(
                {
                    "step": step.name,
                    "type": step.type,
                    "status": step_result["status"],
                    "message": step_result.get("message", ""),
                }
            )

            if step_result["status"] == "done":
                results["steps_done"] += 1
            elif step_result["status"] == "skipped":
                results["steps_skipped"] += 1
            elif step_result["status"] == "failed":
                results["steps_failed"] += 1

            if broadcast_fn:
                await broadcast_fn(
                    {
                        "type": "preset_step",
                        "preset": preset.name,
                        "step_index": i + 1,
                        "step_name": step.name,
                        "step_type": step.type,
                        "status": step_result["status"],
                    }
                )

        if broadcast_fn:
            await broadcast_fn(
                {
                    "type": "preset_finished",
                    "preset": preset.name,
                    "results": results,
                }
            )

        logger.info(
            f"Preset '{preset.name}' terminée — "
            f"{results['steps_done']} ✓ "
            f"{results['steps_skipped']} skipped "
            f"{results['steps_failed']} ✗"
        )

        return results

    async def _execute_step(self, step: PresetStep, index: int, total: int) -> dict:
        logger.debug(f"Step {index}/{total} [{step.type}] : {step.name}")

        handlers = {
            "cli": self._exec_cli,
            "open_app": self._exec_open_app,
            "spotify": self._exec_spotify,
            "tts": self._exec_tts,
            "ai": self._exec_ai,
            "wait": self._exec_wait,
            "notify": self._exec_notify,
        }

        handler = handlers.get(step.type)
        if not handler:
            return {"status": "skipped", "message": f"Type de step inconnu : {step.type}"}

        try:
            return await handler(step)
        except Exception as e:
            collector.error("JRV-SKL-001", "JRV-SKL-001", cause=e)
            logger.error(f"Erreur step '{step.name}': {e}")
            return {"status": "failed", "message": str(e)}

    async def _exec_cli(self, step: PresetStep) -> dict:
        cmd = step.get_command()

        if cmd is None:
            system = platform.system().lower()
            return {"status": "skipped", "message": f"Non supporté sur {system}"}

        logger.debug(f"CLI : {cmd[:80]}")

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode == 0:
            return {"status": "done", "message": stdout.decode()[:200]}
        return {"status": "failed", "message": stderr.decode()[:200]}

    async def _exec_open_app(self, step: PresetStep) -> dict:
        """Ouvre une application seulement si elle n'est pas déjà lancée."""
        target = step.get_command()
        if target is None:
            system = platform.system().lower()
            return {"status": "skipped", "message": f"Non supporté sur {system}"}

        process = step.process or Path(target).stem
        if await _is_process_running(process):
            return {"status": "skipped", "message": f"{process} déjà ouvert"}

        system = platform.system().lower()
        if system == "windows":
            launched = await _start_windows_app(target, list(step.windows_paths))
        elif system == "darwin":
            launched = await _run_shell(target)
        else:
            launched = await _run_shell(target)

        if launched:
            return {"status": "done", "message": f"{process} lancé"}
        return {"status": "failed", "message": f"Impossible de lancer {process}"}

    async def _exec_spotify(self, step: PresetStep) -> dict:
        if not self._tools:
            return {"status": "skipped", "message": "ToolRegistry non disponible"}

        result = await self._tools.call(
            "spotify_control",
            {"action": step.action or "play", "query": step.query or ""},
        )

        if not result.is_error:
            return {"status": "done", "message": result.content}
        return {"status": "failed", "message": result.content}

    async def _exec_tts(self, step: PresetStep) -> dict:
        if not self._tts or not step.text:
            return {"status": "skipped", "message": "TTS non disponible ou texte vide"}

        audio_bytes = await self._tts.synthesize(step.text)

        await broadcast_audio(audio_bytes)

        return {"status": "done", "message": f"TTS : {step.text[:50]}"}

    async def _exec_ai(self, step: PresetStep) -> dict:
        prompt = (step.prompt or "").replace("{user}", settings.display_name)
        if not self._llm or not prompt:
            return {"status": "skipped", "message": "LLM non disponible ou prompt vide"}

        response = await self._llm.complete(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=150,
            system=(
                f"Tu es {settings.display_assistant_name}. "
                "Réponds de façon courte et naturelle, en français."
            ),
        )

        text = response.content if hasattr(response, "content") else str(response)

        if self._tts and text:
            audio_bytes = await self._tts.synthesize(text)

            await broadcast_audio(audio_bytes)

        return {"status": "done", "message": text[:100]}

    async def _exec_wait(self, step: PresetStep) -> dict:
        seconds = max(0, min(step.seconds, 30))
        await asyncio.sleep(seconds)
        return {"status": "done", "message": f"Attente {seconds}s"}

    async def _exec_notify(self, step: PresetStep) -> dict:
        cmd = step.get_command()

        if not cmd:
            system = platform.system().lower()
            if system == "darwin":
                title = step.title.replace('"', '\\"')
                body = step.body.replace('"', '\\"')
                cmd = f'osascript -e \'display notification "{body}" with title "{title}"' + "'"
            elif system == "windows":
                cmd = (
                    f'powershell -c "Add-Type -AssemblyName System.Windows.Forms; '
                    f"[System.Windows.Forms.MessageBox]::Show('{step.body}','{step.title}')\""
                )
            else:
                return {"status": "skipped", "message": "Notifications non supportées sur Linux"}

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
        return {"status": "done", "message": f"Notification : {step.title}"}


_CREATE_NO_WINDOW = 0x08000000


def _win_kwargs() -> dict:
    if platform.system().lower() == "windows":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


async def _is_process_running(name: str) -> bool:
    """True si un process correspondant à `name` tourne déjà."""
    image = name.strip()
    if not image:
        return False
    system = platform.system().lower()
    if system == "windows":
        exe = image if image.lower().endswith(".exe") else f"{image}.exe"
        proc = await asyncio.create_subprocess_exec(
            "tasklist",
            "/FI",
            f"IMAGENAME eq {exe}",
            "/NH",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            **_win_kwargs(),
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
        text = out.decode(errors="replace").lower()
        return exe.lower() in text and "info:" not in text

    proc = await asyncio.create_subprocess_exec(
        "pgrep",
        "-ifl",
        image,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=8)
    return proc.returncode == 0 and bool(out.strip())


async def _run_shell(cmd: str) -> bool:
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.wait_for(proc.communicate(), timeout=20)
    return proc.returncode == 0


async def _start_windows_app(target: str, extra_paths: list[str]) -> bool:
    """Lance une app Windows via un exe existant, sinon `cmd /c start`."""
    candidates: list[str] = []
    for raw in extra_paths:
        expanded = os.path.expandvars(raw)
        if os.path.isfile(expanded):
            candidates.append(expanded)
    if os.path.isfile(os.path.expandvars(target)):
        candidates.append(os.path.expandvars(target))

    launch_target = candidates[0] if candidates else target
    proc = await asyncio.create_subprocess_exec(
        "cmd",
        "/c",
        "start",
        "",
        launch_target,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        **_win_kwargs(),
    )
    await asyncio.wait_for(proc.communicate(), timeout=15)
    return proc.returncode == 0
