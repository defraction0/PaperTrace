"""User-level settings — one small JSON file, and never a reason to fail.

Only the contact email lives here so far. It is the one input that stops a run
before it starts (Unpaywall requires it), and re-typing it for every audit was
the first thing that tripped a first-time user.

**JSON, not TOML, deliberately.** `requires-python` is `>=3.10` and `tomllib`
arrived in 3.11; reaching for TOML would reintroduce the exact version mismatch
that made the 3.10 CI cell unable to collect a test module. `json` is stdlib
everywhere this package claims to run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ENV_OVERRIDE = "PAPERTRACE_CONFIG"


def config_path() -> Path:
    """Where settings live. `PAPERTRACE_CONFIG` overrides it — tests set that
    rather than writing into a real home directory."""
    if override := os.environ.get(ENV_OVERRIDE):
        return Path(override).expanduser()
    root = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(root).expanduser() / "papertrace" / "config.json"


def load() -> dict:
    """Settings, or `{}`.

    Absent, empty, unreadable, malformed, or holding a non-object all mean the
    same thing to a caller: there is nothing here. A stray byte in a dotfile
    must not break commands that never read it, so this cannot raise.
    """
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(**values) -> Path:
    """Merge `values` into the file and return its path.

    Merging, not replacing: a future key must not be erased by a caller that
    only knows about the email.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = {**load(), **values}
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return path
