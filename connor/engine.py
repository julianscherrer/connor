"""The conversation engine: builds prompts, talks to Ollama and runs tools."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .database import Database, keywords
from .files import FILE_EVENT
from .llm import (RECOMMENDED_MODEL, CancelToken, OllamaClient, OllamaError, ThinkingNotSupported,
                  ToolsNotSupported, parse_text_tool_calls, strip_think, strip_tool_markup)
from .prompts import build_context_block, build_system_prompt
from .settings import Settings
from .tools import ToolExecutor, tool_names, tool_schemas

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
TOKEN_RESERVE = 2500  # room for tool definitions and the answer
CHARS_PER_TOKEN = 3
DOC_SHARE = 0.4  # share of the context that file excerpts may use
LIBRARY_CHARS = 2400  # excerpts from files that are not attached to the chat

QUERY_PROMPT = ("(Connor-App: Formuliere für meine letzte Frage eine kurze Websuche mit höchstens 8 Wörtern. "
                "Antworte NUR mit der Suchanfrage, ohne Erklärung.)")
NUDGE_MEMORY = ("(Connor-App, nicht vom User: Du hast geschrieben, dass du dir etwas merkst – aber `save_memory` "
                "wurde nicht aufgerufen, also ist nichts gespeichert. Ruf `save_memory` jetzt auf. "
                "Danach reicht ein kurzer Satz.)")
NUDGE_REMINDER = ("(Connor-App, nicht vom User: Du hast eine Erinnerung zugesagt – aber `create_reminder` wurde "
                  "nicht aufgerufen, also gibt es keine. Ruf `create_reminder` jetzt auf. Danach reicht ein kurzer Satz.)")
NUDGE_FILE = ("(Connor-App, nicht vom User: Es wurde keine Datei erstellt – dafür musst du `create_file` aufrufen. "
              "Ruf `create_file` jetzt auf, mit dem vollständigen Inhalt in content. Danach reicht ein kurzer Satz.)")
SUMMARY_WORDS = re.compile(r"(?i)\b(zusammenfass\w*|fass\w*\s.*zusammen|überblick|worum geht|inhalt\w*|summar\w*)")

_ANNOUNCE = [re.compile(pattern, re.I) for pattern in (
    r"\b(?:ich|lass\s+mich)\b[^.!?\n]{0,50}?\b(?:nachschau\w*|nachseh\w*|nachguck\w*|nachzuschauen|nachzusehen|"
    r"recherchier\w*|googl\w*)",
    r"\b(?:ich|lass\s+mich)\b[^.!?\n]{0,40}?\b(?:such\w*|schau\w*|guck\w*|check\w*)\b[^.!?\n]{0,40}?"
    r"\b(?:im\s+internet|im\s+netz|im\s+web|online|nach)\b",
    r"\b(?:ich|lass\s+mich)\s+(?:schau|guck|such|check)\w*\s+(?:mal|kurz|gleich|schnell|rasch)\b",
    r"\b(?:starte|mache?|führe)\b[^.!?\n]{0,25}?\b(?:websuche|internetsuche|suche|recherche)\b",
    r"\bmoment\b[^\n]{0,80}?\b(?:such|schau|recherch|nachseh|nachschau|guck)",
)]
_MEMORY_CLAIMS = [re.compile(pattern, re.I) for pattern in (
    r"\bhab(?:e|'s)?\b[^.!?\n]{0,40}?\b(?:gemerkt|gespeichert|abgespeichert|notiert)\b",
    r"\bich\s+merk(?:e)?\s+(?:mir|es)\b",
    r"\b(?:ist|sind)\s+(?:jetzt\s+|nun\s+)?(?:gespeichert|notiert|abgespeichert)\b",
    r"\b(?:gemerkt|gespeichert|notiert)\s*[!✓✅]",
)]
_REMINDER_CLAIMS = [re.compile(pattern, re.I) for pattern in (
    r"\bich\b[^.!?\n]{0,30}?\bdich\b[^.!?\n]{0,40}?\berinner\w*",
    r"\bich\s+(?:werde\s+)?erinner\w*\s+dich\b",
    r"\berinnerung\b[^.!?\n]{0,30}?\b(?:gesetzt|erstellt|eingetragen|angelegt|gespeichert|steht)\b",
)]
_FILE_CLAIMS = [re.compile(pattern, re.I) for pattern in (
    r"\b(?:datei|dokument|word\w*|docx)\b[^.!?\n]{0,50}?\b(?:erstellt|gespeichert|angelegt|bereit|fertig|abgelegt)\b",
    r"\bich\s+(?:erstelle|mache|speichere|schreibe|generiere|exportiere)\b[^.!?\n]{0,60}?"
    r"\b(?:datei|word\w*|dokument|docx)\b",
    r"\b(?:hier|da)\s+ist\s+(?:dein|deine|das)\b[^.!?\n]{0,30}?\b(?:word\w*|datei|dokument)\b",
)]
_FILE_REQUEST = re.compile(
    r"(?i)(\bals\s+(?:word|docx|datei|dokument|md|markdown|txt|textdatei)\b|\bword[-\s]?(?:datei|dokument|file)\b"
    r"|\bdocx\b|\b(?:speicher|exportier|erstell|mach|schreib|generier)\w*\b[^.!?\n]{0,50}\b(?:word|datei|dokument)\b)"
)
_NEGATION = re.compile(r"(?i)\b(nicht|nichts|kein\w*|noch nie)\b")
_FILLER = re.compile(
    r"^\s*(?:hey|hallo|hoi|hi|sali|salü|connor|bitte|also|kannst du(?: mir)?|könntest du(?: mir)?|sag mir|"
    r"such(?:e)?(?: mir)?(?: mal)?(?: im internet)?(?: nach)?|schau(?: mal)?(?: nach)?|google(?: mal)?)[\s,:!?.]+",
    re.I,
)
_CONTEXT = re.compile(r"<kontext>.*?</kontext>\s*", re.S)


@dataclass
class Reply:
    text: str
    changes: set[str] = field(default_factory=set)
    model: str = ""
    searched: bool = False


def _ignore(_text: str) -> None:
    pass


def announces_search(text: str) -> bool:
    """True if the model only announced a web search instead of doing it."""
    content = strip_think(text).strip()
    if not content or len(content) > 500:
        return False
    tail = content[-220:]
    return any(pattern.search(tail) for pattern in _ANNOUNCE)


def query_from_announcement(text: str) -> str:
    match = re.search(r"[«„\"“]([^»“”\"\n]{3,80})[»“”\"]", text)
    if match and 1 <= len(match.group(1).split()) <= 12:
        return match.group(1).strip()
    return ""


def _claims(patterns: list[re.Pattern], text: str) -> bool:
    for pattern in patterns:
        for match in pattern.finditer(text):
            if not _NEGATION.search(match.group(0)):
                return True
    return False


def missing_tool_nudge(text: str, used: set[str], user_text: str) -> str:
    """A hidden reminder for the model if it claimed to save something without calling the tool."""
    content = strip_think(text)
    asked = user_text.lower()
    if not {"save_memory", "update_memory"} & used and _claims(_MEMORY_CLAIMS, content):
        if len(content) < 400 or any(w in asked for w in ("merk", "speicher", "notier", "vergiss nicht")):
            return NUDGE_MEMORY
    if "create_reminder" not in used and _claims(_REMINDER_CLAIMS, content):
        if any(w in asked for w in ("erinner", "wecker", "reminder")):
            return NUDGE_REMINDER
    if "create_file" not in used and _FILE_REQUEST.search(user_text) and _claims(_FILE_CLAIMS, content):
        return NUDGE_FILE
    return ""


def clean_query(text: str, limit: int = 14) -> str:
    text = _CONTEXT.sub("", text).strip()
    if text.lower().startswith("/suche"):
        text = text[6:]
    previous = None
    while previous != text:
        previous = text
        text = _FILLER.sub("", text)
    return " ".join(text.split()[:limit]).strip(" ?!.")


def sanitize_query(raw: str, fallback: str) -> str:
    line = next((part.strip() for part in strip_think(raw).splitlines() if part.strip()), "")
    line = re.sub(r"(?i)^(suchanfrage|websuche|suche|query)\s*:\s*", "", line).strip(" \"'«»„“`*")
    if not line or len(line) > 120 or len(line.split()) > 14:
        return fallback
    return line


def _call_parts(call: dict) -> tuple[str, dict]:
    function = call.get("function") or {}
    args = function.get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    return str(function.get("name", "")), args if isinstance(args, dict) else {}


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

    def _options(self, mode: str = "writer", **extra) -> dict:
        temperature = self.settings.chat_temperature if mode == "assistant" else self.settings.temperature
        return {"temperature": temperature, "num_ctx": self.settings.context_size, **extra}

    # ---------------------------------------------------------------- prompts
    def _preferences(self) -> list[dict]:
        return [m for m in self.db.list_memories(category="vorliebe") if m["status"] != "verworfen"][:10]

    def system_prompt(self, mode: str, tool_mode: str) -> str:
        """Stays the same during a chat, so Ollama can keep it in its cache (= faster answers)."""
        return build_system_prompt(
            mode=mode, tool_mode=tool_mode, web=self.settings.web_enabled, user_name=self.settings.user_name,
            preferences=self._preferences(), extra=self.settings.extra_instructions,
        )

    def context_budget(self) -> int:
        return int((self.settings.context_size - TOKEN_RESERVE) * CHARS_PER_TOKEN * DOC_SHARE)

    def document_context(self, query: str, *, project: str = "", doc_ids: list[int] | None = None,
                         budget: int | None = None, library_chars: int = LIBRARY_CHARS) -> list[dict]:
        """Excerpts from the user's files: attached files first, then matching passages from the library."""
        budget = self.context_budget() if budget is None else budget
        selected: list[dict] = []
        used = 0
        attached = list(doc_ids or [])
        if attached:
            docs = [doc for doc in (self.db.get_document(i) for i in attached) if doc]
            if sum(len(doc["content"]) for doc in docs) <= budget:
                for doc in docs:
                    selected.append({"name": doc["name"], "content": doc["content"], "position": None,
                                     "doc_id": doc["id"]})
                    used += len(doc["content"])
            else:
                chunks = [] if SUMMARY_WORDS.search(query) else self.db.search_chunks(query, doc_ids=attached,
                                                                                        limit=24)
                if not chunks:
                    chunks = self._spread_chunks(docs, budget)
                for chunk in chunks:
                    if used + len(chunk["content"]) <= budget:
                        selected.append(chunk)
                        used += len(chunk["content"])
                selected.sort(key=lambda c: (c["doc_id"], c["position"]))
        words = keywords(query)
        room = min(library_chars, budget - used)
        if words and room > 300:
            current = project.strip().casefold()
            for chunk in self.db.search_chunks(query, project=project, exclude=attached, limit=8):
                own_project = bool(current) and chunk["project"].strip().casefold() == current
                if chunk["hits"] < (1 if own_project else min(2, len(words))) or len(chunk["content"]) > room:
                    continue
                selected.append(chunk)
                room -= len(chunk["content"])
                if room < 300:
                    break
        return selected

    def _spread_chunks(self, docs: list[dict], budget: int) -> list[dict]:
        """Passages spread over the whole file – for «summarise this»."""
        per_doc = max(1, budget // max(1, len(docs)) // 1200)
        picked = []
        for doc in docs:
            chunks = self.db.document_chunks(doc["id"])
            if len(chunks) <= per_doc:
                picked += chunks
                continue
            step = (len(chunks) - 1) / max(1, per_doc - 1)
            picked += [chunks[round(i * step)] for i in range(per_doc)]
        return picked

    def build_messages(self, chat_id: int, *, tools: bool) -> list[dict]:
        chat = self.db.get_chat(chat_id) or {}
        project, mode = chat.get("project", ""), chat.get("mode", "writer")
        history = [m for m in self.db.get_messages(chat_id) if m["role"] in ("user", "assistant")]
        user_texts = [m["content"] for m in history if m["role"] == "user"]
        last_user = user_texts[-1] if user_texts else ""
        query = last_user if len(last_user.split()) >= 6 or len(user_texts) < 2 else f"{user_texts[-2]} {last_user}"

        system = self.system_prompt(mode, "tools" if tools else "plain")
        preference_ids = {m["id"] for m in self._preferences()}
        memories = [m for m in self.db.search_memories(f"{query} {project}", project=project, limit=8,
                                                       include_discarded=False) if m["id"] not in preference_ids]
        chunks = self.document_context(query, project=project, doc_ids=[d["id"] for d in self.db.chat_documents(chat_id)])
        context = build_context_block(now=datetime.now(), project=project, projects=self.db.list_projects()[:20],
                                      memories=memories, chunks=chunks)

        budget = max(4000, (self.settings.context_size - TOKEN_RESERVE) * CHARS_PER_TOKEN - len(system) - len(context))
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
        if selected and selected[-1]["role"] == "user":
            selected[-1] = {"role": "user", "content": f"{context}\n\n{selected[-1]['content']}"}
        return [{"role": "system", "content": system}, *selected]

    # ------------------------------------------------------------- answering
    def respond(self, chat_id: int, *, on_chunk: Callable[[str], None] = _ignore,
                on_event: Callable[[str], None] = _ignore, token: CancelToken | None = None,
                confirm: Callable[[str], bool] | None = None, force_web: bool = False,
                web_query: str | None = None) -> Reply:
        """Answers the latest user message of a chat. Tools are executed automatically.
        ``force_web`` searches the internet first (the 🌐 button)."""
        token = token or CancelToken()
        client = self.new_client()
        model = self.resolve_model(client)
        tools_on = self.tools_enabled_for(client, model)
        chat = self.db.get_chat(chat_id) or {}
        mode = chat.get("mode", "writer")
        web_on = self.settings.web_enabled
        executor = ToolExecutor(self.db, self.settings, project=chat.get("project", ""), confirm=confirm,
                                fallback_content=lambda: self._file_fallback(parts, previous_answer))
        messages = self.build_messages(chat_id, tools=tools_on)
        stored = self.db.get_messages(chat_id)
        last_user = next((m["content"] for m in reversed(stored) if m["role"] == "user"), "")
        last_user_index = max((i for i, m in enumerate(stored) if m["role"] == "user"), default=len(stored))
        previous_answer = next((m["content"] for m in reversed(stored[:last_user_index]) if m["role"] == "assistant"), "")
        parts: list[str] = []
        searched = False
        web_block = ""

        if force_web and web_on:
            query = web_query or self._search_query(client, model, messages, tools_on, mode, token)
            if token.cancelled:
                return Reply("", set(), model)
            on_event(executor.describe("web_search", {"query": query}))
            web_block = f"\n\n<websuche>\n{executor.execute('web_search', {'query': query})}\n</websuche>"
            messages[-1] = {**messages[-1], "content": messages[-1]["content"] + web_block}
            searched = True

        used_tools: set[str] = set()
        rounds = 0
        nudged = False
        while not token.cancelled:
            use_tools = tools_on and rounds < MAX_TOOL_ROUNDS
            schemas = tool_schemas(web_on) if use_tools else None
            try:
                content, calls = self._stream(client, model, messages, schemas, mode, on_chunk, token,
                                              prefix="\n\n" if parts else "")
            except ToolsNotSupported:
                if not tools_on:
                    raise
                log.info("Modell %s kann keine Werkzeuge – ich mache ohne weiter.", model)
                self._no_tools.add(model)
                tools_on = False
                messages = self.build_messages(chat_id, tools=False)
                if web_block:
                    messages[-1] = {**messages[-1], "content": messages[-1]["content"] + web_block}
                continue
            rounds += 1
            if use_tools and not calls:
                calls = parse_text_tool_calls(content, tool_names(web_on))
            visible = strip_tool_markup(content).strip()
            if visible:
                parts.append(visible)
            if token.cancelled:
                break

            if calls and use_tools:
                messages.append({"role": "assistant", "content": content, "tool_calls": calls})
                for call in calls:
                    if token.cancelled:
                        break
                    name, args = _call_parts(call)
                    if name != "create_file":
                        on_event(executor.describe(name, args))
                    result = executor.execute(name, args)
                    used_tools.add(name)
                    searched = searched or name == "web_search"
                    if name == "create_file" and result.startswith("OK: Datei erstellt: "):
                        on_event(FILE_EVENT + result.removeprefix("OK: Datei erstellt: "))
                    log.info("Werkzeug %s: %s", name, result[:200].replace("\n", " "))
                    tool_message = {"role": "tool", "tool_name": name, "content": result}
                    if call.get("id"):
                        tool_message["tool_call_id"] = call["id"]
                    messages.append(tool_message)
                continue

            if web_on and not searched and announces_search(content):
                log.info("Modell hat eine Suche nur angekündigt – Connor sucht selbst.")
                query = query_from_announcement(content) or self._search_query(
                    client, model, [*messages, {"role": "assistant", "content": content}], tools_on, mode, token)
                if token.cancelled:
                    break
                on_event(executor.describe("web_search", {"query": query}))
                result = executor.execute("web_search", {"query": query})
                searched = True
                if use_tools:
                    messages.append({"role": "assistant", "content": content, "tool_calls": [
                        {"function": {"name": "web_search", "arguments": {"query": query}}}]})
                    messages.append({"role": "tool", "tool_name": "web_search", "content": result})
                else:
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": f"<websuche>\n{result}\n</websuche>\n\n"
                                     "(Connor-App: Hier sind die Suchergebnisse. Beantworte jetzt meine Frage damit.)"})
                continue

            if use_tools and not nudged:
                nudge = missing_tool_nudge(content, used_tools, last_user)
                if nudge:
                    log.info("Modell hat Speichern behauptet, ohne das Werkzeug zu nutzen – hake nach.")
                    nudged = True
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": nudge})
                    continue
            break
        return Reply("\n\n".join(parts), set(executor.changed), model, searched)

    def _search_query(self, client: OllamaClient, model: str, messages: list[dict], tools_on: bool, mode: str,
                      token: CancelToken) -> str:
        """Lets the model phrase a good search query. Uses the same prompt beginning as the answer
        itself, so Ollama's cache makes this cheap."""
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        fallback = clean_query(last_user) or "aktuelle Nachrichten"
        schemas = tool_schemas(self.settings.web_enabled) if tools_on else None
        try:
            content, calls = self._stream(client, model, [*messages, {"role": "user", "content": QUERY_PROMPT}],
                                          schemas, mode, _ignore, token, num_predict=60, temperature=0.2)
        except OllamaError as exc:
            log.info("Suchanfrage konnte nicht formuliert werden: %s", exc)
            return fallback
        for call in calls:
            name, args = _call_parts(call)
            query = str(args.get("query") or "").strip()
            if name == "web_search" and query:
                return query
        return sanitize_query(content, fallback)

    @staticmethod
    def _file_fallback(parts: list[str], previous_answer: str) -> str:
        """«Speicher das als Word»: use what Connor wrote in this turn or, if that is short, his last answer."""
        current = "\n\n".join(parts).strip()
        return current if len(current) >= 200 else previous_answer

    def generate(self, system: str, prompt: str, *, on_chunk: Callable[[str], None] = _ignore,
                 token: CancelToken | None = None) -> Reply:
        """A single answer without tools (used by the writing desk)."""
        token = token or CancelToken()
        client = self.new_client()
        model = self.resolve_model(client)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        content, _ = self._stream(client, model, messages, None, "writer", on_chunk, token)
        return Reply(content.strip(), set(), model)

    def writing_system_prompt(self) -> str:
        return build_system_prompt(mode="writer", tool_mode="writing", user_name=self.settings.user_name,
                                   preferences=self._preferences(), extra=self.settings.extra_instructions)

    def writing_context(self, project: str, query: str) -> str:
        memories: list[dict] = []
        if project.strip():
            own = [m for m in self.db.list_memories(project=project) if m["status"] != "verworfen"]
            own.sort(key=lambda m: 0 if m["status"] == "kanon" else 1)
            memories = own[:15]
        seen = {m["id"] for m in memories} | {m["id"] for m in self._preferences()}
        for memory in self.db.search_memories(query, project=project, limit=6, include_discarded=False):
            if memory["id"] not in seen:
                memories.append(memory)
                seen.add(memory["id"])
        chunks = self.document_context(query, project=project, library_chars=4000)
        return build_context_block(now=datetime.now(), project=project, memories=memories, chunks=chunks)

    def warm_up(self) -> str:
        """Loads the model and pre-reads Connor's instructions, so the first answer comes faster."""
        client = self.new_client()
        model = self.resolve_model(client)
        mode = self.settings.default_mode
        tools_on = self.tools_enabled_for(client, model)
        token = CancelToken()
        try:
            system = self.system_prompt(mode, "tools" if tools_on else "plain")
            schemas = tool_schemas(self.settings.web_enabled) if tools_on else None
            self._stream(client, model, [{"role": "system", "content": system}], schemas, mode, _ignore, token,
                         num_predict=1)
        except ToolsNotSupported:
            self._no_tools.add(model)
            self._stream(client, model, [{"role": "system", "content": self.system_prompt(mode, "plain")}], None,
                         mode, _ignore, token, num_predict=1)
        return model

    def _stream(self, client: OllamaClient, model: str, messages: list[dict], tools: list[dict] | None, mode: str,
                on_chunk: Callable[[str], None], token: CancelToken, prefix: str = "",
                **extra_options) -> tuple[str, list[dict]]:
        think = None if model in self._no_think else self.settings.think_value()
        content: list[str] = []
        calls: list[dict] = []
        try:
            for kind, data in client.chat_stream(model, messages, tools=tools, options=self._options(mode, **extra_options),
                                                 think=think, keep_alive=self.settings.keep_alive or None, token=token):
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
            return self._stream(client, model, messages, tools, mode, on_chunk, token, prefix, **extra_options)
        return strip_think("".join(content)), calls
