"""The conversation engine: builds prompts, talks to Ollama and runs tools."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .database import Database
from .llm import (RECOMMENDED_MODEL, CancelToken, OllamaClient, OllamaError, ThinkingNotSupported,
                  ToolsNotSupported, strip_think)
from .prompts import build_system_prompt
from .settings import Settings
from .tools import ToolExecutor, tool_schemas

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
TOKEN_RESERVE = 2500  # room for tool definitions and the answer
CHARS_PER_TOKEN = 3


@dataclass
class Reply:
    text: str
    changes: set[str] = field(default_factory=set)
    model: str = ""


def _ignore(_text: str) -> None:
    pass


class ConnorEngine:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings
        self._capabilities: dict[tuple[str, str], list[str] | None] = {}
        self._no_tools: set[str] = set()
        self._no_think: set[str] = set()

    def new_client(self) -> OllamaClient:
        return OllamaClient(self.settings.ollama_url)

    def resolve_model(self, client: OllamaClient) -> str:
        if self.settings.model.strip():
            return self.settings.model.strip()
        models = client.list_models()
        if not models:
            raise OllamaError(
                "Es ist noch kein KI-Modell installiert. Öffne ein Terminal und tippe:  "
                f"ollama pull {RECOMMENDED_MODEL}"
            )
        return models[0]

    def tools_enabled_for(self, client: OllamaClient, model: str) -> bool:
        if not self.settings.use_tools or model in self._no_tools:
            return False
        key = (client.base_url, model)
        if key not in self._capabilities:
            self._capabilities[key] = client.capabilities(model)
        caps = self._capabilities[key]
        return caps is None or "tools" in caps

    def _options(self) -> dict:
        return {"temperature": self.settings.temperature, "num_ctx": self.settings.context_size}

    # ---------------------------------------------------------------- prompts
    def _preferences(self) -> list[dict]:
        return [m for m in self.db.list_memories(category="vorliebe") if m["status"] != "verworfen"][:10]

    def chat_system_prompt(self, project: str, query: str, mode: str) -> str:
        preferences = self._preferences()
        known = {m["id"] for m in preferences}
        memories = [m for m in self.db.search_memories(query, project=project, limit=8, include_discarded=False)
                    if m["id"] not in known]
        return build_system_prompt(
            now=datetime.now(), user_name=self.settings.user_name, project=project,
            projects=self.db.list_projects()[:20], memories=memories, preferences=preferences,
            mode=mode, web=self.settings.web_enabled, extra=self.settings.extra_instructions,
        )

    def writing_system_prompt(self, project: str, query: str) -> str:
        memories: list[dict] = []
        if project.strip():
            own = [m for m in self.db.list_memories(project=project) if m["status"] != "verworfen"]
            own.sort(key=lambda m: 0 if m["status"] == "kanon" else 1)
            memories = own[:15]
        seen = {m["id"] for m in memories}
        preferences = self._preferences()
        seen |= {m["id"] for m in preferences}
        for memory in self.db.search_memories(query, project=project, limit=6, include_discarded=False):
            if memory["id"] not in seen:
                memories.append(memory)
                seen.add(memory["id"])
        return build_system_prompt(
            now=datetime.now(), user_name=self.settings.user_name, project=project,
            memories=memories, preferences=preferences, mode="writing",
            web=False, extra=self.settings.extra_instructions,
        )

    def build_messages(self, chat_id: int, *, tools: bool, augment: str = "") -> list[dict]:
        chat = self.db.get_chat(chat_id) or {}
        project = chat.get("project", "")
        history = [m for m in self.db.get_messages(chat_id) if m["role"] in ("user", "assistant")]
        last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        system = self.chat_system_prompt(project, f"{last_user} {project}", "tools" if tools else "plain")

        budget = max(4000, (self.settings.context_size - TOKEN_RESERVE) * CHARS_PER_TOKEN - len(system))
        selected: list[dict] = []
        used = 0
        for message in reversed(history):
            content = message["content"]
            if selected and used + len(content) > budget:
                break
            if len(content) > budget:
                content = "[…] " + content[-budget:]
            selected.append({"role": message["role"], "content": content})
            used += len(content)
        selected.reverse()
        if augment and selected and selected[-1]["role"] == "user":
            selected[-1] = {"role": "user", "content": f"{selected[-1]['content']}\n\n{augment}"}
        return [{"role": "system", "content": system}, *selected]

    # ------------------------------------------------------------- answering
    def respond(self, chat_id: int, *, on_chunk: Callable[[str], None] = _ignore,
                on_event: Callable[[str], None] = _ignore, token: CancelToken | None = None,
                confirm: Callable[[str], bool] | None = None, augment: str = "") -> Reply:
        """Answers the latest user message of a chat. Tools are executed automatically."""
        token = token or CancelToken()
        client = self.new_client()
        model = self.resolve_model(client)
        tools_on = self.tools_enabled_for(client, model)
        chat = self.db.get_chat(chat_id) or {}
        executor = ToolExecutor(self.db, self.settings, project=chat.get("project", ""), confirm=confirm)
        messages = self.build_messages(chat_id, tools=tools_on, augment=augment)

        parts: list[str] = []
        rounds = 0
        while not token.cancelled:
            use_tools = tools_on and rounds < MAX_TOOL_ROUNDS
            schemas = tool_schemas(self.settings.web_enabled) if use_tools else None
            try:
                content, calls = self._stream(client, model, messages, schemas, on_chunk, token,
                                              prefix="\n\n" if parts else "")
            except ToolsNotSupported:
                if not tools_on:
                    raise
                log.info("Modell %s kann keine Werkzeuge – ich mache ohne weiter.", model)
                self._no_tools.add(model)
                tools_on = False
                messages = self.build_messages(chat_id, tools=False, augment=augment)
                continue
            rounds += 1
            if content.strip():
                parts.append(content.strip())
            if token.cancelled or not calls or not use_tools:
                break
            messages.append({"role": "assistant", "content": content, "tool_calls": calls})
            for call in calls:
                if token.cancelled:
                    break
                function = call.get("function") or {}
                name = str(function.get("name", ""))
                args = function.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                on_event(executor.describe(name, args))
                result = executor.execute(name, args)
                log.info("Werkzeug %s: %s", name, result[:200].replace("\n", " "))
                tool_message = {"role": "tool", "tool_name": name, "content": result}
                if call.get("id"):
                    tool_message["tool_call_id"] = call["id"]
                messages.append(tool_message)
        return Reply("\n\n".join(parts), set(executor.changed), model)

    def generate(self, system: str, prompt: str, *, on_chunk: Callable[[str], None] = _ignore,
                 token: CancelToken | None = None) -> Reply:
        """A single answer without tools (used by the writing desk)."""
        token = token or CancelToken()
        client = self.new_client()
        model = self.resolve_model(client)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        content, _ = self._stream(client, model, messages, None, on_chunk, token)
        return Reply(content.strip(), set(), model)

    def _stream(self, client: OllamaClient, model: str, messages: list[dict], tools: list[dict] | None,
                on_chunk: Callable[[str], None], token: CancelToken, prefix: str = "") -> tuple[str, list[dict]]:
        think = None if model in self._no_think else self.settings.think_value()
        content: list[str] = []
        calls: list[dict] = []
        try:
            for kind, data in client.chat_stream(model, messages, tools=tools, options=self._options(),
                                                 think=think, token=token):
                if kind == "content":
                    if prefix and not content:
                        on_chunk(prefix)
                    content.append(str(data))
                    on_chunk(str(data))
                elif kind == "tool_call" and isinstance(data, dict):
                    calls.append(data)
        except ThinkingNotSupported:
            if think is None:
                raise
            self._no_think.add(model)
            return self._stream(client, model, messages, tools, on_chunk, token, prefix)
        return strip_think("".join(content)), calls
