"""Project attribution for Claude Code events (O9 C4). Stdlib only, no git subprocess.

Order for the hook's cwd: configured alias (longest matching workspace root) -> sanitized git
`origin` slug -> git root directory name -> fallback `claude-code`. Only the resolved name and its
source leave this module; paths and remote URLs are never returned or logged. Slug sanitization and
name normalization match the plugin's project.py (shared vectors: tests/fixtures/attribution_vectors.json).
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from typing import Optional

FALLBACK = "claude-code"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_INVALID_LABEL_RE = re.compile(r"[^a-z0-9._-]+")
_IPV4_RE = re.compile(r"^[0-9]{1,3}(\.[0-9]{1,3}){3}$")
_ORIGIN_RE = re.compile(r'^\s*\[\s*remote\s+"origin"\s*\]\s*$')
_SECTION_RE = re.compile(r"^\s*\[")
_URL_RE = re.compile(r"^\s*url\s*=\s*(.*?)\s*$", re.IGNORECASE)


def normalize_name(value: str) -> Optional[str]:
    """Plugin-compatible normalization; None instead of the plugin's `hermes` fallback."""
    text = _INVALID_LABEL_RE.sub("-", str(value or "").strip().lower()).strip("-._")[:64]
    return text if text and _NAME_RE.fullmatch(text) else None


def slug_from_url(url: str) -> Optional[str]:
    """Last path component of a remote URL (https, scp-like, with credentials), `.git` removed."""
    clean = str(url).strip().rstrip("/").removesuffix(".git")
    slug = clean.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return normalize_name(slug)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _alias(cwd: Path, aliases: dict[str, str]) -> Optional[str]:
    table = {}
    for root, name in aliases.items():
        try:
            table[_norm(str(Path(root).expanduser()))] = name
        except Exception:
            continue
    for candidate in (cwd, *cwd.parents):  # nearest first = longest matching prefix
        name = table.get(_norm(str(candidate)))
        if name:
            return name
    return None


def _git_root(cwd: Path) -> Optional[Path]:
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _git_dir(root: Path) -> Optional[Path]:
    dot = root / ".git"
    if dot.is_dir():
        return dot
    try:
        first = dot.read_bytes().decode("utf-8").splitlines()[0]
    except Exception:
        return None
    if not first.startswith("gitdir:"):
        return None
    target = Path(first[len("gitdir:"):].strip())
    return target if target.is_absolute() else (root / target)


def _origin_url(root: Path) -> Optional[str]:
    git_dir = _git_dir(root)
    if git_dir is None:
        return None
    config = git_dir / "config"
    try:  # a worktree's gitdir holds `commondir`; its config lives there
        common = (git_dir / "commondir").read_bytes().decode("utf-8").strip()
        if common:
            base = Path(common)
            config = (base if base.is_absolute() else git_dir / base) / "config"
    except Exception:
        pass
    try:
        lines = config.read_bytes().decode("utf-8", "replace").splitlines()
    except Exception:
        return None
    in_origin = False
    for line in lines:
        if _SECTION_RE.match(line):
            in_origin = bool(_ORIGIN_RE.match(line))
            continue
        if in_origin:
            match = _URL_RE.match(line)
            if match:
                return match.group(1).strip('"')
    return None


def _host_names() -> set[str]:
    names: set[str] = set()
    try:
        host = socket.gethostname().strip().lower()
        if host:
            names.update({host, host.split(".", 1)[0]})
        fqdn = socket.getfqdn().strip().lower()
        if fqdn:
            names.add(fqdn)
    except Exception:
        pass
    return names


def _acceptable(name: Optional[str], hosts: set[str]) -> bool:
    return (bool(name) and bool(_NAME_RE.fullmatch(name)) and name not in hosts
            and not _IPV4_RE.fullmatch(name))


def resolve(cwd: object, aliases: dict[str, str]) -> tuple[str, str]:
    """(project name, source) with source in alias / git_remote / git_root / fallback. Never raises."""
    try:
        if not isinstance(cwd, str) or not cwd.strip():
            return FALLBACK, "fallback"
        path = Path(cwd)
        hosts = _host_names()
        name = _alias(path, aliases)
        if _acceptable(name, hosts):
            return name, "alias"
        root = _git_root(path)
        if root is not None:
            url = _origin_url(root)
            name = slug_from_url(url) if url else None
            if _acceptable(name, hosts):
                return name, "git_remote"
            name = normalize_name(root.name)
            if _acceptable(name, hosts):
                return name, "git_root"
    except Exception:
        pass
    return FALLBACK, "fallback"
