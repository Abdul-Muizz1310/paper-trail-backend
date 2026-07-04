"""Prompt loader for agents/prompts/*.md."""

from __future__ import annotations

from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent.parent / "agents" / "prompts"


@cache
def load(name: str) -> str:
    """Load a prompt file by name, stripping optional YAML frontmatter.

    Prompt files are immutable at runtime, so each is read + parsed once per
    process rather than re-read on every node invocation (OPT-3: no blocking
    disk I/O on the event loop per round).
    """
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt not found: {name} (looked in {path})")
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        rest = text[3:]
        end = rest.find("\n---")
        text = rest[end + 4 :].lstrip("\n") if end != -1 else rest
    return text.strip()
