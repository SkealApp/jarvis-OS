# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""WhisperSTT — plugin STT local 100 % offline pour le pipeline LiveKit.

Utilise faster-whisper (CTranslate2) qui tourne sur CPU sans GPU requis.
Modèles disponibles : tiny / base / small / medium / large-v3.
  tiny  — ~75 Mo, 1–2s de latence, suffisant pour la commande vocale
  small — ~240 Mo, meilleure précision, 2–4s de latence
  large-v3 — ~1.5 Go, précision maximale

Sélection via WHISPER_MODEL dans .env (défaut : tiny).

Ce module implémente l'interface `livekit.agents.stt.STT` et renvoie un
`SpeechEvent` avec le texte transcrit. `StreamAdapter` de LiveKit convertit
automatiquement cet adaptateur batch en pipeline streaming temps réel.
"""

from __future__ import annotations

import os
from math import gcd
from typing import TYPE_CHECKING

import numpy as np
from loguru import logger

if TYPE_CHECKING:
    pass

from livekit.agents import NOT_GIVEN, APIConnectOptions, NotGivenOr, utils as lk_utils
from livekit.agents.stt import (
    STT,
    STTCapabilities,
    SpeechData,
    SpeechEvent,
    SpeechEventType,
)

_DEFAULT_MODEL = "tiny"
_SAMPLE_RATE = 16_000  # faster-whisper attend du 16 kHz


class WhisperSTT(STT):
    """STT local via faster-whisper (CTranslate2, CPU, offline)."""

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        language: str = "fr",
        compute_type: str = "int8",
        device: str = "cpu",
    ) -> None:
        super().__init__(
            capabilities=STTCapabilities(
                streaming=False,
                interim_results=False,
            )
        )
        self._language = language
        self._model_name = model
        self._compute_type = compute_type
        self._device = device
        self._model = None  # chargé au premier appel (lazy) pour ne pas bloquer l'import

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]

            logger.info(
                "WhisperSTT: chargement du modèle %s (%s/%s)…",
                self._model_name,
                self._device,
                self._compute_type,
            )
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            self._model = WhisperModel(
                self._model_name,
                device=self._device,
                compute_type=self._compute_type,
            )
            logger.info("WhisperSTT: modèle %s prêt", self._model_name)
        except Exception as exc:
            raise RuntimeError(
                f"Impossible de charger faster-whisper ({self._model_name}): {exc}"
            ) from exc

    async def _recognize_impl(
        self,
        buffer: lk_utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> SpeechEvent:
        import asyncio

        lang = language if language is not NOT_GIVEN else self._language  # type: ignore[misc]

        def _transcribe() -> str:
            self._load_model()
            assert self._model is not None

            frames = lk_utils.merge_frames(buffer)
            src_rate: int = frames.sample_rate  # LiveKit WebRTC = 48 000 Hz

            # Sécurité : buffer vide → retour silencieux
            raw = bytes(frames.data)
            if not raw:
                logger.debug("WhisperSTT: buffer vide, skip")
                return ""

            # PCM int16 → float32 [-1, 1] (format attendu par faster-whisper)
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

            # Rééchantillonnage si la source n'est pas déjà à 16 kHz
            if src_rate != _SAMPLE_RATE:
                from scipy.signal import resample_poly  # type: ignore[import-untyped]

                g = gcd(src_rate, _SAMPLE_RATE)
                audio = resample_poly(audio, _SAMPLE_RATE // g, src_rate // g)
                logger.debug(
                    "WhisperSTT: rééch. %d Hz → %d Hz (%d samples)",
                    src_rate,
                    _SAMPLE_RATE,
                    len(audio),
                )

            segments, _info = self._model.transcribe(
                audio,
                language=lang or None,
                beam_size=5,
                vad_filter=True,
            )
            return " ".join(seg.text.strip() for seg in segments).strip()

        text = await asyncio.get_event_loop().run_in_executor(None, _transcribe)
        logger.debug("WhisperSTT: '%s'", text)

        return SpeechEvent(
            type=SpeechEventType.FINAL_TRANSCRIPT,
            alternatives=[
                SpeechData(
                    language=lang or "fr",
                    text=text,
                    confidence=1.0,
                )
            ],
        )
