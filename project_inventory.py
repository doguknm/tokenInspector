from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

_PROJECT_SAFE = re.compile(r"[^a-z0-9._-]+")
_DEFAULT_MAX_REPOS = 200


def normalize_project_name(value: str, fallback: str = "unknown") -> str:
    text = str(value or "").strip().lower()
    normalized = _PROJECT_SAFE.sub("-", text)[:80].strip("-")
    return normalized or fallback


def _project_roots() -> tuple[Path, ...]:
    raw = os.getenv("TOKEN_INSPECTOR_PROJECT_ROOTS", "~/Projects")
    roots: list[Path] = []
    for value in raw.split(os.pathsep):
        if not value.strip():
            continue
        path = Path(value.strip()).expanduser()
        try:
            path = path.resolve()
        except OSError:
            pass
        if path.is_dir() and path not in roots:
            roots.append(path)
    return tuple(roots)


def _remote_slug(repo: Path) -> str | None:
    try:
        remote = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    clean = remote.rstrip("/").removesuffix(".git")
    slug = clean.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    normalized = normalize_project_name(slug, "")
    return normalized or None


def discover_projects() -> list[dict]:
    try:
        max_repos = min(max(int(os.getenv("TOKEN_INSPECTOR_MAX_PROJECTS", _DEFAULT_MAX_REPOS)), 1), 1000)
    except ValueError:
        max_repos = _DEFAULT_MAX_REPOS

    projects: list[dict] = []
    for root in _project_roots():
        try:
            children = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name.casefold())
        except OSError:
            continue
        for repo in children:
            if len(projects) >= max_repos:
                return projects
            if not (repo / ".git").exists():
                continue
            remote_slug = _remote_slug(repo)
            projects.append(
                {
                    "project_name": remote_slug or normalize_project_name(repo.name),
                    "directory_name": repo.name,
                    "discovery_source": "git_remote" if remote_slug else "git_root",
                    "workspace_id": hashlib.sha256(str(repo).encode("utf-8")).hexdigest()[:16],
                }
            )
    return projects
