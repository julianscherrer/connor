"""Startet Connor – deinen lokalen KI-Kumpel."""
import sys


def main() -> int:
    if sys.version_info < (3, 10):
        print("Connor braucht Python 3.10 oder neuer.")
        return 1
    try:
        from connor.gui.app import run
    except ImportError as exc:
        print(f"Es fehlt ein Paket: {exc}")
        print("Installiere alles mit:  pip install -r requirements.txt   (oder starte einfach start.bat)")
        return 1
    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
