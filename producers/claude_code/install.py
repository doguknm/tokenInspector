"""Install / uninstall the Token Inspector Claude Code hooks in ~/.claude/settings.json (O9 C9). Stdlib only.

    python install.py [--settings PATH] [--dry-run | --apply] [--uninstall]

Default is --dry-run: a content-free operation summary, nothing written. It never prints the settings
file, a diff of it, or any other hook's command. --apply writes a local backup first
(`settings.json.bak-ti-<UTC stamp>`, never committed or synced), then writes atomically only if the file
is unchanged since it was read. Our entries are identified by the exact command this installer builds for
this machine; every other key and hook entry (e.g. PossibleSkills' PreToolUse hook) is preserved. The
whole apply runs under PossibleSkills' OS lock file `settings.json.lock-possibleskills`, so the two
installers never write concurrently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

EVENTS = ("Stop", "SubagentStop", "SessionEnd")
HOOK_TIMEOUT_S = 10
LOCK_SUFFIX = ".lock-possibleskills"
LOCK_WAIT_S = 10
HOOK_PATH = Path(__file__).resolve().with_name("cc_hook.py")
# Test hook: called between computing the result and the unchanged-source re-read.
_before_write: Optional[Callable[[], None]] = None


class InstallError(Exception):
    """Carries a fixed message only (never file content)."""


def hook_command(platform: str = sys.platform, hook_path: Path = HOOK_PATH) -> str:
    interpreter = "python" if platform == "win32" else "python3"
    return f'{interpreter} "{hook_path.as_posix()}"'


def _entry(command: str) -> dict[str, Any]:
    return {"matcher": "", "hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_S}]}


def _is_ours(hook: Any, command: str) -> bool:
    return isinstance(hook, dict) and hook.get("command") == command  # exact match, never a suffix


def _hooks_of(group: Any) -> list:
    hooks = group.get("hooks") if isinstance(group, dict) else None
    return hooks if isinstance(hooks, list) else []


def transform(data: dict[str, Any], command: str, uninstall: bool) -> tuple[dict[str, Any], dict[str, int]]:
    """Merge (or remove) our three entries; returns (new settings, content-free counts)."""
    if not isinstance(data, dict):
        raise InstallError("settings file is not a JSON object; nothing written")
    counts = {"added": 0, "already_present": 0, "removed": 0, "foreign_hook_entries_preserved": 0,
              "other_top_level_keys_preserved": sum(1 for k in data if k != "hooks")}
    added = {event: 0 for event in EVENTS}
    hooks = data.get("hooks")
    if hooks is None:
        if uninstall:
            return data, dict(counts, **{f"add_{e}": 0 for e in EVENTS})
        hooks = data["hooks"] = {}
    if not isinstance(hooks, dict):
        raise InstallError("settings 'hooks' is not an object; nothing written")
    for event, groups in hooks.items():
        if isinstance(groups, list):
            counts["foreign_hook_entries_preserved"] += sum(
                1 for g in groups for h in _hooks_of(g) if not _is_ours(h, command))
    for event in EVENTS:
        groups = hooks.get(event)
        if groups is not None and not isinstance(groups, list):
            raise InstallError("settings hook event is not a list; nothing written")
        present = any(_is_ours(h, command) for g in (groups or []) for h in _hooks_of(g))
        if uninstall:
            if not groups:
                continue
            kept = []
            for group in groups:
                inner = _hooks_of(group)
                ours = [h for h in inner if _is_ours(h, command)]
                if not ours:
                    kept.append(group)
                    continue
                counts["removed"] += len(ours)
                rest = [h for h in inner if not _is_ours(h, command)]
                if rest:  # mixed container: only our hook object leaves, the container stays
                    group["hooks"] = rest
                    kept.append(group)
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
        elif present:
            counts["already_present"] += 1
        else:
            hooks.setdefault(event, []).append(_entry(command))
            added[event] = 1
            counts["added"] += 1
    if uninstall and not hooks and counts["removed"]:
        del data["hooks"]
    counts.update({f"add_{e}": added[e] for e in EVENTS})
    return data, counts


def _encode(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _read(path: Path) -> bytes:
    return path.read_bytes() if path.exists() else b""


def _parse(raw: bytes) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise InstallError("settings file is not valid JSON; nothing written") from None


@contextmanager
def settings_lock(settings: Path):
    """PossibleSkills' OS lock (msvcrt byte lock on Windows, flock elsewhere); released if the process dies."""
    lock_path = settings.with_name(settings.name + LOCK_SUFFIX)
    fh = open(lock_path, "a+b")
    deadline = time.monotonic() + LOCK_WAIT_S
    locked = False
    try:
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise InstallError("settings lock is held by another installer; nothing written") from None
                time.sleep(0.1)
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        fh.close()


def _write_atomic(path: Path, payload: bytes) -> None:
    tmp = path.with_name(f"{path.name}.ti-{os.getpid()}-{secrets.token_hex(6)}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise InstallError("settings file could not be written; nothing changed") from None


def run(settings: Path, *, apply: bool, uninstall: bool, platform: str = sys.platform) -> dict[str, int]:
    hook_path = HOOK_PATH
    if any(char in hook_path.as_posix() for char in '$`"%!\n'):
        raise InstallError("hook path contains shell metacharacters; nothing changed")
    command = hook_command(platform, hook_path)

    def plan() -> tuple[bytes, bytes, dict[str, int]]:
        raw = _read(settings)
        new, counts = transform(_parse(raw), command, uninstall)
        return raw, _encode(new), counts

    if not apply:
        return plan()[2]
    settings.parent.mkdir(parents=True, exist_ok=True)
    with settings_lock(settings):
        raw, payload, counts = plan()
        changed = counts["added"] or counts["removed"]
        if not changed:
            return counts
        if _before_write is not None:
            _before_write()
        if hashlib.sha256(_read(settings)).digest() != hashlib.sha256(raw).digest():
            raise InstallError("settings file changed since it was read (another session?); nothing written")
        if settings.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            for n in range(100):  # exclusive: a second apply in the same second never overwrites a backup
                backup = settings.with_name(f"{settings.name}.bak-ti-{stamp}" + (f"-{n}" if n else ""))
                try:
                    with open(backup, "xb") as fh:
                        fh.write(raw)
                    break
                except FileExistsError:
                    continue
                except OSError:
                    try:
                        backup.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise InstallError("backup could not be written; nothing changed") from None
            else:
                raise InstallError("backup could not be written; nothing changed")
        _write_atomic(settings, payload)
    return counts


def summary(counts: dict[str, int], *, apply: bool, uninstall: bool, settings: Path) -> str:
    mode = "apply" if apply else "dry-run"
    if uninstall:
        head = f"uninstall {mode}: remove {counts['removed']} of our hook entries"
    else:
        head = "install {}: add {}; already_present={}".format(
            mode, " ".join(f"{e}={counts[f'add_{e}']}" for e in EVENTS), counts["already_present"])
    return (f"{head}; foreign_hook_entries_preserved={counts['foreign_hook_entries_preserved']}; "
            f"other_top_level_keys_preserved={counts['other_top_level_keys_preserved']}; settings={settings}")


def _say(text: str) -> None:
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None) or "utf-8"
    stream.write(text.encode(encoding, "replace").decode(encoding) + "\n")
    stream.flush()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Token Inspector Claude Code hook installer")
    parser.add_argument("--settings", default=None)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    settings = Path(args.settings) if args.settings else Path.home() / ".claude" / "settings.json"
    try:
        counts = run(settings, apply=args.apply, uninstall=args.uninstall)
    except InstallError as exc:
        _say(str(exc))
        return 1
    _say(summary(counts, apply=args.apply, uninstall=args.uninstall, settings=settings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
