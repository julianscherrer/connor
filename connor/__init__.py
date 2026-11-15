"""Connor – dein lokaler KI-Kumpel für Geschichten, Ideen und den Alltag."""

APP_NAME = "Connor"
__version__ = "1.3.0"


def build_id() -> str:
    """Changes whenever Connor's code on disk changes (e.g. after an update) – so a newer Connor
    can replace one that is still running with the old code."""
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parent
    digest = hashlib.sha1(__version__.encode())
    for path in sorted(root.rglob("*.py")):
        stat = path.stat()
        digest.update(f"{path.relative_to(root)}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:12]
