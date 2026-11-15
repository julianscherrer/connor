"""A tiny stand-in for Ollama, so the tests run without a real AI model."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeOllama:
    """Each entry in ``script`` answers one /api/chat request:
    {"content": "...", "tool_calls": [...], "thinking": "...", "delay": 0.01} or {"error": "...", "status": 400}.
    A callable entry receives the request payload and returns such a dict."""

    def __init__(self, models=("qwen3:8b",), capabilities=("completion", "tools")):
        self.models = list(models)
        # a list (same for every model) or a dict model -> list
        if isinstance(capabilities, dict):
            self.capabilities = {name: list(caps) for name, caps in capabilities.items()}
        else:
            self.capabilities = list(capabilities) if capabilities is not None else None
        self.script: list = []
        self.requests: list[dict] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeOllama":
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _handler(fake):  # noqa: N805 - closure over the fake instance
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _json(self, status: int, data: dict) -> None:
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _line(self, data: dict) -> None:
                self.wfile.write(json.dumps(data).encode() + b"\n")
                self.wfile.flush()

            def do_GET(self):
                if self.path == "/api/tags":
                    self._json(200, {"models": [{"name": m} for m in fake.models]})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/show":
                    if fake.capabilities is None:
                        self._json(200, {})
                    else:
                        caps = fake.capabilities
                        if isinstance(caps, dict):
                            caps = caps.get(payload.get("model") or payload.get("name"), ["completion"])
                        self._json(200, {"capabilities": caps})
                    return
                if self.path != "/api/chat":
                    self._json(404, {"error": "not found"})
                    return
                fake.requests.append(payload)
                step = fake.script.pop(0) if fake.script else {"content": "Okay!"}
                if callable(step):
                    step = step(payload)
                if "error" in step:
                    self._json(step.get("status", 400), {"error": step["error"]})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                delay = step.get("delay", 0)
                model = payload.get("model", "")
                try:
                    if step.get("thinking"):
                        self._line({"model": model, "message": {"role": "assistant", "content": "",
                                                                "thinking": step["thinking"]}, "done": False})
                    content = step.get("content", "")
                    pieces = [content[i:i + 7] for i in range(0, len(content), 7)]
                    for piece in pieces:
                        if delay:
                            time.sleep(delay)
                        self._line({"model": model, "message": {"role": "assistant", "content": piece},
                                    "done": False})
                    if step.get("tool_calls"):
                        self._line({"model": model, "message": {"role": "assistant", "content": "",
                                                                "tool_calls": step["tool_calls"]}, "done": False})
                    self._line({"model": model, "message": {"role": "assistant", "content": ""}, "done": True,
                                "done_reason": "stop"})
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        return Handler


def tool_call(name: str, **arguments) -> dict:
    return {"function": {"name": name, "arguments": arguments}}
