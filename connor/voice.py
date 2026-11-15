"""Speech to text with faster-whisper. Your voice never leaves this PC –
only the speech model is downloaded once (into the «models» folder)."""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import threading
from pathlib import Path
from typing import Callable

from .paths import models_dir

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
# Plain HTTPS download instead of the Xet client: works behind proxies and with the Windows certificates.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
MAX_SECONDS = 600
VOICE_MODELS = [
    ("tiny", "Tiny – 75 MB, sehr schnell, eher ungenau"),
    ("base", "Base – 145 MB, schnell"),
    ("small", "Small – 480 MB, gut (Empfehlung)"),
    ("medium", "Medium – 1.5 GB, genauer, langsamer"),
    ("large-v3-turbo", "Large v3 Turbo – 1.6 GB, sehr genau (ideal mit Grafikkarte)"),
]
MODEL_SIZES_MB = {"tiny": 75, "base": 145, "small": 485, "medium": 1530, "large-v3-turbo": 1620}
VOICE_LANGUAGES = [("de", "Deutsch"), ("auto", "Automatisch erkennen"), ("en", "Englisch"),
                   ("fr", "Französisch"), ("it", "Italienisch")]
INSTALL_HINT = ("Die Spracherkennung ist noch nicht installiert. Starte Connor einmal über start.bat – "
                "dann richtet er sie ein (ca. 300 MB).")
SILENCE_HINT = ("Ich habe nur Stille aufgenommen. Prüfe in Windows: Einstellungen → Datenschutz und Sicherheit → "
                "Mikrofon → «Desktop-Apps den Zugriff auf das Mikrofon erlauben».")

# Whisper sometimes "hears" TV credits in silence.
_HALLUCINATION = re.compile(
    r"(?i)^\W*(untertitel\w*\b.*|(vielen )?dank fürs zu(schauen|sehen)\W*|copyright (wdr|swr|zdf|ard).*|"
    r"(swr|wdr|zdf) \d{4}\W*)$"
)


class VoiceError(Exception):
    """Problem with microphone or speech recognition, written for the user."""


def missing_packages(need_mic: bool = True) -> list[str]:
    names = ["faster_whisper"] + (["sounddevice"] if need_mic else [])
    return [name for name in names if importlib.util.find_spec(name) is None]


def vad_available() -> bool:
    """The optional silence detection of faster-whisper needs onnxruntime (not available for every Python)."""
    return importlib.util.find_spec("onnxruntime") is not None


def trim_silence(audio, frame_ms: int = 30, pad_ms: int = 300):
    """Cuts silence at the start and end – our own little stand-in when onnxruntime is missing."""
    import numpy as np

    frame = SAMPLE_RATE * frame_ms // 1000
    count = len(audio) // frame
    if count < 3:
        return audio
    rms = np.sqrt(np.mean(audio[: count * frame].reshape(count, frame) ** 2, axis=1))
    loud = np.nonzero(rms > max(0.004, float(rms.max()) * 0.08))[0]
    if not len(loud):
        return audio[:0]
    pad = pad_ms // frame_ms
    start = max(0, int(loud[0]) - pad) * frame
    end = min(count, int(loud[-1]) + 1 + pad) * frame
    return audio[start:end]


def decode_audio(path: str):
    """Reads any audio or video file as 16 kHz mono float32 (works with old and new PyAV versions)."""
    import av
    import numpy as np

    try:
        container = av.open(path, mode="r", metadata_errors="ignore")
    except TypeError:
        container = av.open(path, mode="r")
    chunks = []
    with container:
        if not container.streams.audio:
            raise VoiceError("Die Datei hat keine Tonspur.")
        resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(audio=0):
            try:
                chunks += [part.to_ndarray().reshape(-1) for part in resampler.resample(frame)]
            except av.error.InvalidDataError:
                continue
        chunks += [part.to_ndarray().reshape(-1) for part in resampler.resample(None)]
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


class Recorder:
    """Records the default microphone into memory (mono, 16 kHz)."""

    def __init__(self) -> None:
        self._chunks: list = []
        self._stream = None
        self._rate = SAMPLE_RATE
        self._lock = threading.Lock()
        self._np = None
        self.level = 0.0

    def start(self) -> None:
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as exc:
            raise VoiceError(INSTALL_HINT) from exc
        except OSError as exc:
            raise VoiceError(f"Das Mikrofon-Modul konnte nicht geladen werden: {exc}") from exc
        self._np = np

        def callback(indata, _frames, _time, _status) -> None:
            samples = indata[:, 0].copy()
            with self._lock:
                self._chunks.append(samples)
            self.level = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0

        try:
            device = sd.query_devices(kind="input")
        except Exception as exc:  # noqa: BLE001
            raise VoiceError("Ich finde kein Mikrofon. Ist eins angeschlossen und in Windows aktiviert?") from exc
        last_error: Exception | None = None
        for rate in dict.fromkeys((SAMPLE_RATE, int(device.get("default_samplerate") or 0))):
            if not rate:
                continue
            try:
                stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32", callback=callback,
                                        blocksize=max(256, rate // 10))
                stream.start()
            except Exception as exc:  # noqa: BLE001 - try the device's own sample rate next
                last_error = exc
                continue
            self._stream, self._rate = stream, rate
            return
        raise VoiceError(f"Das Mikrofon lässt sich nicht öffnen: {last_error}")

    @property
    def seconds(self) -> float:
        with self._lock:
            frames = sum(len(chunk) for chunk in self._chunks)
        return frames / self._rate

    def stop(self):
        """Ends the recording and returns the audio as float32 array at 16 kHz."""
        self._close()
        np = self._np
        with self._lock:
            chunks, self._chunks = self._chunks, []
        audio = np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, dtype=np.float32)
        if self._rate != SAMPLE_RATE and len(audio):
            target = int(len(audio) * SAMPLE_RATE / self._rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, target), np.arange(len(audio)), audio)
            audio = audio.astype(np.float32)
        return audio

    def cancel(self) -> None:
        self._close()
        with self._lock:
            self._chunks = []

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001
                pass


class Transcriber:
    """Turns speech into text. Loads the Whisper model on first use and keeps it in memory."""

    def __init__(self, model_name: str = "small", language: str = "de", device: str = "auto",
                 root: Path | None = None):
        self._lock = threading.Lock()
        self._model = None
        self._loaded: tuple[str, str] | None = None
        self._cuda_failed = False
        self._vad_failed = False
        self.root = root
        self.configure(model_name, language, device)

    def configure(self, model_name: str, language: str, device: str) -> None:
        self.model_name = model_name if model_name in MODEL_SIZES_MB else "small"
        self.language = language or "de"
        self.device = device if device in ("auto", "cpu", "cuda") else "auto"

    def folder(self) -> Path:
        path = self.root or models_dir() / "whisper"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def is_ready(self) -> bool:
        return self._model is not None and self._loaded is not None and self._loaded[0] == self.model_name

    def downloaded_mb(self) -> float:
        try:
            return sum(p.stat().st_size for p in self.folder().rglob("*") if p.is_file()) / 1_000_000
        except OSError:
            return 0.0

    def _devices(self) -> list[tuple[str, str]]:
        cpu = [("cpu", "int8")]
        if self.device == "cpu" or self._cuda_failed:
            return cpu
        try:
            import ctranslate2

            if ctranslate2.get_cuda_device_count() > 0:
                return [("cuda", "float16"), *cpu]
        except Exception:  # noqa: BLE001
            pass
        return cpu

    def _load(self, device: str, compute_type: str):
        from faster_whisper import WhisperModel

        return WhisperModel(self.model_name, device=device, compute_type=compute_type,
                            download_root=str(self.folder()))

    def transcribe(self, audio, progress: Callable[[float], None] | None = None,
                   should_stop: Callable[[], bool] | None = None) -> str:
        """``audio`` is a float32 array (16 kHz, mono)."""
        if missing_packages(need_mic=False):
            raise VoiceError(INSTALL_HINT)
        with self._lock:
            last_error: Exception | None = None
            for device, compute_type in self._devices():
                try:
                    if not self.is_ready() or self._loaded != (self.model_name, device):
                        self._model = None
                        self._model = self._load(device, compute_type)
                        self._loaded = (self.model_name, device)
                    return self._run(audio, progress, should_stop)
                except Exception as exc:  # noqa: BLE001 - GPU problems: fall back to the processor
                    last_error = exc
                    self._model = None
                    self._loaded = None
                    if device == "cuda":
                        log.warning("Spracherkennung auf der Grafikkarte klappt nicht (%s) – nutze den Prozessor.", exc)
                        self._cuda_failed = True
                        continue
            size = MODEL_SIZES_MB.get(self.model_name, 500)
            if last_error is not None and not any(self.folder().rglob("model.bin")):
                raise VoiceError(f"Das Sprachmodell konnte nicht geladen werden. Beim ersten Mal lädt Connor es "
                                 f"aus dem Internet (ca. {size} MB) – bist du online? ({last_error})") from last_error
            raise VoiceError(f"Die Spracherkennung hat nicht geklappt: {last_error}") from last_error

    def _run(self, audio, progress, should_stop) -> str:
        language = None if self.language == "auto" else self.language
        use_vad = vad_available() and not self._vad_failed
        if not use_vad:
            audio = trim_silence(audio)
            if not len(audio):
                return ""
        try:
            segments, info = self._model.transcribe(audio, language=language, vad_filter=use_vad, beam_size=5)
        except Exception as exc:  # noqa: BLE001 - a broken onnxruntime must not stop the dictation
            if not use_vad or "onnxruntime" not in str(exc).lower():
                raise
            log.warning("Stille-Erkennung nicht verfügbar (%s) – transkribiere ohne.", exc)
            self._vad_failed = True
            return self._run(audio, progress, should_stop)
        duration = float(getattr(info, "duration", 0) or 0)
        texts = []
        for segment in segments:
            if should_stop is not None and should_stop():
                break
            text = segment.text.strip()
            if text and not _HALLUCINATION.match(text):
                texts.append(text)
            if progress is not None and duration:
                progress(min(1.0, segment.end / duration))
        return " ".join(texts).strip()

    def file_transcriber(self) -> Callable[[Path, Callable[[str], None]], str]:
        """For audio files that are dropped into Connor."""

        def run(path: Path, progress: Callable[[str], None]) -> str:
            progress(f"🎧 Lese {path.name} …")
            try:
                audio = decode_audio(str(path))
            except VoiceError:
                raise
            except Exception as exc:  # noqa: BLE001 - damaged or unsupported audio
                raise VoiceError(f"Die Audiodatei konnte nicht gelesen werden ({exc.__class__.__name__}).") from exc
            progress(f"🎧 Transkribiere {path.name} … (das kann dauern)")
            text = self.transcribe(audio, progress=lambda part: progress(
                f"🎧 Transkribiere {path.name} … {int(part * 100)} %"))
            if not text:
                raise VoiceError("In der Aufnahme habe ich keine Sprache erkannt.")
            return text

        return run
