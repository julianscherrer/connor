"""Talks to the local Ollama server – Connor's brain."""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Iterator

import requests

log = logging.getLogger(__name__)

DEFAULT_URL = "http://localhost:11434"
OLLAMA_DOWNLOAD = "https://ollama.com/download"
RECOMMENDED_MODEL = "qwen3:8b"


class OllamaError(Exception):
    """Error that can be shown to the user as is."""


class OllamaUnavailable(OllamaError):
    pass


class ModelNotFound(OllamaError):
    pass


class ToolsNotSupported(OllamaError):
    pass


class ThinkingNotSupported(OllamaError):
    pass


def unavailable_message(base_url: str) -> str:
    return (
        f"Ich erreiche Ollama nicht unter {base_url}. Läuft Ollama? "
        f"Falls es noch nicht installiert ist: {OLLAMA_DOWNLOAD}"
    )


def classify_error(message: str, model: str) -> OllamaError:
    low = message.lower()
    if "does not support tools" in low:
        return ToolsNotSupported(message)
    if "does not support thinking" in low or "think value" in low:
        return ThinkingNotSupported(message)
    if "not found" in low and ("model" in low or model.lower() in low):
        return ModelNotFound(
            f"Das Modell «{model}» ist nicht installiert. Lade es im Terminal mit:  ollama pull {model}"
        )
    return OllamaError(f"Ollama meldet einen Fehler: {message}")


_THINK_BLOCK = re.compile(r"<think>.*?(?:</think>|$)", re.S)


def strip_think(text: str) -> str:
    """Hides the inner monologue of reasoning models."""
    if "<think>" in text:
        return _THINK_BLOCK.sub("", text).lstrip()
    if "</think>" in text:
        return text.rsplit("</think>", 1)[1].lstrip()
    return text


class CancelToken:
    """Lets the UI stop a running answer, even while waiting for data."""

    def __init__(self) -> None:
        self.cancelled = False
        self._response: requests.Response | None = None
        self._lock = threading.Lock()

    def attach(self, response: requests.Response) -> None:
        with self._lock:
            self._response = response
            cancelled = self.cancelled
        if cancelled:
            _close_quietly(response)

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            response = self._response
        if response is not None:
            _close_quietly(response)


def _close_quietly(response: requests.Response) -> None:
    try:
        response.close()
    except Exception:  # noqa: BLE001
        pass


def _error_text(resp: requests.Response) -> str:
    try:
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            return str(data["error"])
    except ValueError:
        pass
    return f"HTTP {resp.status_code}: {resp.text[:200]}"


class OllamaClient:
    def __init__(self, base_url: str = DEFAULT_URL):
        self.base_url = (base_url or DEFAULT_URL).strip().rstrip("/")
        self.session = requests.Session()
        # Ollama runs locally – never send these requests through a proxy.
        self.session.trust_env = False

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def list_models(self, timeout: float = 5) -> list[str]:
        try:
            resp = self.session.get(self._url("/api/tags"), timeout=timeout)
            resp.raise_for_status()
            models = resp.json().get("models", [])
        except (requests.RequestException, ValueError) as exc:
            raise OllamaUnavailable(unavailable_message(self.base_url)) from exc
        names = [m.get("name") or m.get("model") for m in models if isinstance(m, dict)]
        return sorted((n for n in names if n and "embed" not in n.lower()), key=str.lower)

    def capabilities(self, model: str, timeout: float = 10) -> list[str] | None:
        """What the model can do (tools, thinking, ...). None if Ollama does not say."""
        try:
            resp = self.session.post(self._url("/api/show"), json={"model": model}, timeout=timeout)
            if resp.status_code != 200:
                return None
            caps = resp.json().get("capabilities")
        except (requests.RequestException, ValueError):
            return None
        return [str(c) for c in caps] if isinstance(caps, list) else None

    def chat_stream(self, model: str, messages: list[dict], *, tools: list[dict] | None = None,
                    options: dict | None = None, think: bool | None = None,
                    token: CancelToken | None = None) -> Iterator[tuple[str, object]]:
        """Yields ("content", str), ("thinking", str) and ("tool_call", dict)."""
        payload: dict = {"model": model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options
        if think is not None:
            payload["think"] = think
        try:
            resp = self.session.post(self._url("/api/chat"), json=payload, stream=True, timeout=(10, 600))
        except requests.RequestException as exc:
            if token is not None and token.cancelled:
                return
            raise OllamaUnavailable(unavailable_message(self.base_url)) from exc
        if token is not None:
            token.attach(resp)
        try:
            if resp.status_code != 200:
                raise classify_error(_error_text(resp), model)
            for line in resp.iter_lines():
                if token is not None and token.cancelled:
                    return
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if data.get("error"):
                    raise classify_error(str(data["error"]), model)
                message = data.get("message") or {}
                if message.get("thinking"):
                    yield "thinking", message["thinking"]
                if message.get("content"):
                    yield "content", message["content"]
                for call in message.get("tool_calls") or []:
                    yield "tool_call", call
                if data.get("done"):
                    return
        except OllamaError:
            raise
        except Exception as exc:
            if token is not None and token.cancelled:
                return
            if isinstance(exc, (requests.RequestException, OSError)):
                raise OllamaError(f"Die Verbindung zu Ollama ist abgebrochen ({exc.__class__.__name__}).") from exc
            raise
        finally:
            _close_quietly(resp)
