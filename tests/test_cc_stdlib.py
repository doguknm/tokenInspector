"""The Claude Code producer is stdlib-only (O9 AC2.11)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PRODUCER_DIR = Path(__file__).resolve().parents[1] / "producers" / "claude_code"


def test_cc_producer_is_stdlib_only():
    files = sorted(PRODUCER_DIR.glob("*.py"))
    siblings = {p.stem for p in files}
    assert {"cc_hook", "cc_config", "cc_transcript", "cc_events", "cc_attribution", "cc_state", "cc_client",
            "cc_agent", "cc_complexity", "install"} <= siblings
    bad = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    bad.append((path.name, "relative import"))
                    continue
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top not in siblings and top != "__future__":
                    bad.append((path.name, top))
    assert bad == []
