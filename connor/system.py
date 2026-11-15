"""Small OS helpers: start with Windows, start Ollama, and find a Connor that is still running."""
from __future__ import annotations

import getpass
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from .paths import APP_DIR

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Connor"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def server_name() -> str:
    """Name of the channel a running Connor listens on (one per Windows user)."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return f"connor-assistant-{user}"


def instance_running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return server_name() in os.listdir("\\\\.\\pipe\\")
    except OSError:
        return False


def own_venv() -> Path | None:
    """Connor's own .venv – only processes started from there are ever stopped."""
    try:
        venv = Path(sys.prefix).resolve()
        if sys.prefix != sys.base_prefix and venv == (APP_DIR / ".venv").resolve():
            return venv
    except OSError:
        pass
    return None


def connor_processes() -> list[int]:
    """Other processes that run from Connor's .venv (e.g. an older Connor in the tray)."""
    venv = own_venv()
    if venv is None or sys.platform != "win32":
        return []
    prefix = str(venv).replace("'", "''") + "\\"
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith('"
              + prefix + "', [StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { $_.ProcessId }")
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return []
    own = {os.getpid(), os.getppid()}
    return [int(token) for token in result.stdout.split() if token.isdigit() and int(token) not in own]


def stop_processes(pids: list[int]) -> None:
    for pid in pids:
        log.info("Beende alten Connor-Prozess %s", pid)
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=_NO_WINDOW)


def stop_running_connor(wait: float = 4.0) -> bool:
    """For updates: asks a running Connor to quit and stops it if it does not. True if one was running."""
    if sys.platform != "win32" or own_venv() is None:
        return False
    try:
        with open(f"\\\\.\\pipe\\{server_name()}", "wb", buffering=0) as pipe:
            pipe.write(b"quit\n")
    except FileNotFoundError:
        return False
    except OSError:
        pass
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline and instance_running():
        time.sleep(0.3)
    stop_processes(connor_processes())
    return True


def autostart_supported() -> bool:
    return sys.platform == "win32"


def autostart_command() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    return f'"{exe}" "{APP_DIR / "main.py"}" --minimized'


def autostart_enabled() -> bool:
    if not autostart_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if not autostart_supported():
        return
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_VALUE)
            except FileNotFoundError:
                pass


def find_ollama() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    if sys.platform == "win32":
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.exists():
            return str(candidate)
    return None


def try_start_ollama(base_url: str) -> bool:
    """Starts «ollama serve» in the background. Only for a local Ollama."""
    host = urlparse(base_url).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1"):
        return False
    exe = find_ollama()
    if not exe:
        return False
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    try:
        subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError as exc:
        log.warning("Ollama konnte nicht gestartet werden: %s", exc)
        return False
    log.info("Ollama wird gestartet: %s serve", exe)
    return True


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
