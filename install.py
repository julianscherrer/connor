"""Installs and updates Connor's packages. Called by start.bat, update.bat and start.sh.

    python install.py              install / update everything
    python install.py --if-needed  only if something changed since the last installation
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

# Raise this number when the requirements change – start.bat then installs again automatically.
SETUP_VERSION = "3"
ROOT = Path(__file__).resolve().parent
MARKER = Path(sys.prefix) / ".installed"

Runner = Callable[..., bool]


def stop_running_connor() -> bool:
    """A running Connor would keep using the old code and lock files – stop it before installing."""
    try:
        from connor import system

        return system.stop_running_connor()
    except Exception:  # noqa: BLE001 - never block the installation
        return False


def pip(*args: str) -> bool:
    # Brand-new Python versions often have no ready-made PyYAML yet. Without a C compiler its optional
    # speed-up cannot be built – so let PyYAML install as plain Python (Connor does not need the speed-up).
    env = dict(os.environ, PYYAML_FORCE_LIBYAML="0")
    return subprocess.call([sys.executable, "-m", "pip", *args, "--disable-pip-version-check"], env=env) == 0


def install_voice(run: Runner = pip) -> str:
    """Returns "full", "lite" (without onnxruntime) or "" if it did not work."""
    if run("install", "-r", str(ROOT / "requirements-voice.txt")):
        return "full"
    print("\n Die volle Spracherkennung passt (noch) nicht zu dieser Python-Version.")
    print(" Ich installiere sie ohne onnxruntime – das Diktieren funktioniert trotzdem ...\n")
    if run("install", "-r", str(ROOT / "requirements-voice-lite.txt")) and \
            run("install", "--no-deps", "faster-whisper>=1.1"):
        return "lite"
    return ""


def _pause() -> None:
    if sys.stdin is not None and sys.stdin.isatty():
        try:
            input(" Weiter mit Enter ...")
        except EOFError:
            pass


def main(argv: list[str] | None = None, run: Runner = pip, marker: Path = MARKER,
         stop: Callable[[], bool] = stop_running_connor) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    if "--if-needed" in argv:
        try:
            if marker.read_text(encoding="utf-8").strip() == SETUP_VERSION:
                return 0
        except OSError:
            pass

    if stop():
        print("\n Connor lief noch im Hintergrund – ich habe es für das Update beendet.")
    print("\n Installiere bzw. aktualisiere Connors Pakete ...\n")
    run("install", "--upgrade", "pip")
    if not run("install", "-r", str(ROOT / "requirements.txt")):
        print("\n FEHLER: Connors Grundpakete liessen sich nicht installieren.")
        print(" Prüfe die Internetverbindung und starte nochmal.\n")
        return 1

    print("\n Installiere die Spracherkennung für das Diktieren (ca. 300 MB) ...\n")
    voice = install_voice(run)
    if voice == "lite":
        print("\n Spracherkennung installiert (ohne die optionale Stille-Erkennung onnxruntime,")
        print(" die es für deine Python-Version noch nicht gibt). Diktieren funktioniert.\n")
    elif not voice:
        print("\n Hinweis: Die Spracherkennung liess sich nicht installieren.")
        print(" Connor läuft trotzdem, nur ohne Diktieren. Später nochmal versuchen mit update.bat.\n")
        _pause()
    marker.write_text(SETUP_VERSION, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
