from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    version: str
    content: str


@lru_cache(maxsize=16)
def load_prompt(name: str) -> PromptTemplate:
    safe_name = Path(name).name
    if safe_name != name or not safe_name.endswith(".md"):
        raise ValueError("Prompt名が正しくありません。")
    path = PROMPT_DIR / safe_name
    raw = path.read_text(encoding="utf-8")
    first, separator, content = raw.partition("\n---\n")
    if not separator or not first.startswith("version:"):
        raise ValueError(f"Prompt versionがありません: {safe_name}")
    version = first.split(":", 1)[1].strip()
    if not version or not content.strip():
        raise ValueError(f"Promptが空です: {safe_name}")
    return PromptTemplate(name=safe_name, version=version, content=content.strip())
