"""Clickable file links in the terminal output (Ctrl+click in Windows Terminal, VS Code, most Linux/macOS terminals)."""

from __future__ import annotations

from pathlib import Path


def link(path: Path | str) -> str:
    """file:///C:/Users/... for a file or folder: terminals show it as a link that opens it."""
    try:
        return Path(path).resolve().as_uri()
    except (OSError, ValueError):
        return str(path)
