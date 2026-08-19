# Copyright (C) 2026 Barthélemy Houot
# This file is part of Jarvis OS, licensed under the GNU AGPL-3.0-or-later.
# See the LICENSE file or <https://www.gnu.org/licenses/agpl-3.0.html>.

"""EdgeTTS — plugin TTS local/gratuit pour le pipeline LiveKit.

Utilise Microsoft Edge TTS (edge-tts) qui appelle l'API TTS de Microsoft
via le protocole WebSocket du navigateur Edge, sans clé API.

Voix françaises disponibles :
  fr-FR-DeniseNeural   — voix féminine (défaut)
  fr-FR-HenriNeural    — voix masculine
  fr-BE-CharlineNeural — accent belge
  fr-CA-SylvieNeural   — accent québécois

Le MP3 reçu est décodé en PCM 24 kHz mono via PyAV (FFmpeg) puis transmis
à LiveKit en raw PCM via AudioEmitter.
"""

from __future__ import annotations

import asyncio
import io
from math import gcd

import numpy as np
from loguru import logger
from livekit import rtc
from livekit.agents import APIConnectOptions, tts
from livekit.agents.tts import AudioEmitter, SynthesizedAudio, TTSCapabilities

_DEFAULT_VOICE = "fr-FR-DeniseNeural"
_SAMPLE_RATE = 24_000
_NUM_CHANNELS = 1


def _decode_mp3_to_pcm(mp3_data: bytes, target_rate: int) -> np.ndarray:
    """Décode un buffer MP3 en numpy float32 rééchantillonné à target_rate Hz."""
    import av  # type: ignore[import-untyped]

    samples: list[np.ndarray] = []
    src_rate: int | None = None

    with av.open(io.BytesIO(mp3_data), format="mp3") as container:
        stream = container.streams.audio[0]
        src_rate = stream.codec_context.sample_rate

        for frame in container.decode(stream):
            arr = frame.to_ndarray()
            if arr.ndim > 1:
                arr = arr.mean(axis=0)
            # Normaliser si int16
            if "s16" in frame.format.name:
                arr = arr.astype(np.float32) / 32768.0
            else:
                arr = arr.astype(np.float32)
            samples.append(arr)

    if not samples or src_rate is None:
        return np.zeros(0, dtype=np.float32)

    audio = np.concatenate(samples)

    if src_rate != target_rate:
        from scipy.signal import resample_poly  # type: ignore[import-untyped]

        g = gcd(src_rate, target_rate)
        audio = resample_poly(audio, target_rate // g, src_rate // g)

    return audio.astype(np.float32)


class EdgeTTSChunkedStream(tts.ChunkedStream):
    """Stream de synthèse via edge-tts + décodage MP3 PyAV."""

    def __init__(
        self,
        *,
        tts_instance: "EdgeTTS",
        input_text: str,
        conn_options: APIConnectOptions,
        voice: str,
    ) -> None:
        super().__init__(tts=tts_instance, input_text=input_text, conn_options=conn_options)
        self._voice = voice

    async def _run(self, output_emitter: AudioEmitter) -> None:
        import edge_tts

        request_id = lk_utils_shortuuid()
        text = self._input_text.strip()

        output_emitter.initialize(
            request_id=request_id,
            sample_rate=_SAMPLE_RATE,
            num_channels=_NUM_CHANNELS,
            mime_type="audio/pcm",
        )

        if not text:
            logger.debug("EdgeTTS: texte vide, skip")
            return

        try:
            communicate = edge_tts.Communicate(text, voice=self._voice)
            mp3_chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3_chunks.append(chunk["data"])

            if not mp3_chunks:
                logger.warning("EdgeTTS: aucun audio reçu pour '%s'", text[:50])
                return

            mp3_data = b"".join(mp3_chunks)

            pcm_float32 = await asyncio.get_event_loop().run_in_executor(
                None, _decode_mp3_to_pcm, mp3_data, _SAMPLE_RATE
            )

            if len(pcm_float32) == 0:
                logger.warning("EdgeTTS: décodage MP3 vide pour '%s'", text[:50])
                return

            pcm_int16 = (pcm_float32 * 32767).clip(-32767, 32767).astype(np.int16)
            output_emitter.push(pcm_int16.tobytes())

            duration_ms = len(pcm_int16) * 1000 // _SAMPLE_RATE
            logger.debug("EdgeTTS: '%s' → %d ms", text[:40], duration_ms)

        except Exception as exc:
            logger.error("EdgeTTS: erreur synthèse — %s", exc)
            raise


def lk_utils_shortuuid() -> str:
    """Génère un court UUID compatible avec livekit.agents.utils.shortuuid."""
    try:
        from livekit.agents import utils as lk_utils  # type: ignore[import-untyped]

        return lk_utils.shortuuid()
    except Exception:
        import uuid

        return uuid.uuid4().hex[:12]


class EdgeTTS(tts.TTS):
    """Plugin TTS LiveKit utilisant Microsoft Edge TTS (sans clé API)."""

    def __init__(self, *, voice: str = _DEFAULT_VOICE) -> None:
        super().__init__(
            capabilities=TTSCapabilities(streaming=False),
            sample_rate=_SAMPLE_RATE,
            num_channels=_NUM_CHANNELS,
        )
        self._voice = voice
        logger.info("EdgeTTS initialisé — voix %s", voice)

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = APIConnectOptions(),
    ) -> EdgeTTSChunkedStream:
        return EdgeTTSChunkedStream(
            tts_instance=self,
            input_text=text,
            conn_options=conn_options,
            voice=self._voice,
        )
