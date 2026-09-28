"""O10 AC13: the allowlist is documented (literal presence only; the semantic checklist belongs to the review)."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _read(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Body of the markdown section whose heading line contains `heading`, up to the next same-level heading."""
    match = re.search(rf"^(#+) [^\n]*{re.escape(heading)}[^\n]*$", text, re.M)
    assert match, heading
    level = match.group(1)
    rest = text[match.end():]
    end = re.search(rf"^{level} ", rest, re.M)
    return rest[: end.start()] if end else rest


def _rp9(text: str) -> str:
    start = text.index("9. **Task prompts never arrive")
    end = text.index("\n10. ", start)
    return text[start:end]


def test_allowlist_documented():
    readme, agents = _read("README.md"), _read("AGENTS.md")
    assert "TASK_PROMPT_ALLOWED_PROJECTS" in readme and "TASK_PROMPT_ALLOWED_PROJECTS" in agents
    assert "no_allowed_projects" in readme and "task_prompt_allowlist" in readme
    adr = _read("docs/adr/002-task-prompt-retention-and-jev.md")
    for literal in ("task_prompt_deny_path_globs", "D10", "claude -p"):
        assert literal in adr, literal
    assert "allowlist" in _rp9(_read("CLAUDE.md"))
    unreleased = _section(_read("CHANGELOG.md"), "Unreleased")
    assert any("allowlist" in line.lower() for line in unreleased.splitlines() if line.startswith("- "))
