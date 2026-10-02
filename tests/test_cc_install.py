"""Claude Code hook installer (O9 C9; AC2.9). Temp settings files only — never the real ~/.claude/settings.json."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cc_support import PRODUCER_DIR

SECRET = "sk-ant-CANARYSECRET0123"
URL_CANARY = "https://zz-canary-host.internal/hook"
PATH_CANARY = "C:\\Users\\ZZ-CANARY-USER\\secret\\ws"
INSTALL = PRODUCER_DIR / "install.py"


@pytest.fixture
def inst():
    return importlib.reload(importlib.import_module("install"))


def foreign_settings() -> dict:
    return {
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
                                                         "command": f"python coord.py --token {SECRET} {URL_CANARY}"}]}],
            "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": f"notify {PATH_CANARY}"}]}],
        },
        "statusLine": {"type": "command", "command": "status.sh"},
        "enabledPlugins": {"x@y": True},
    }


def write(path: Path, data) -> bytes:
    raw = (json.dumps(data, indent=2) + "\n").encode()
    path.write_bytes(raw)
    return raw


def cli(settings: Path, *args: str):
    result = subprocess.run([sys.executable, str(INSTALL), "--settings", str(settings), *args],
                            capture_output=True, timeout=60)
    return result.returncode, result.stdout + result.stderr


def ours(inst, platform=sys.platform) -> str:
    return inst.hook_command(platform)


def test_installer_dry_run_changes_nothing(tmp_path):
    settings = tmp_path / "settings.json"
    raw = write(settings, foreign_settings())
    code, out = cli(settings)
    assert code == 0 and settings.read_bytes() == raw
    assert b"install dry-run: add Stop=1 SubagentStop=1 SessionEnd=1; already_present=0" in out
    assert b"foreign_hook_entries_preserved=2; other_top_level_keys_preserved=3" in out
    assert not list(tmp_path.glob("settings.json.bak-ti-*"))


def test_installer_output_is_content_free(tmp_path):
    settings = tmp_path / "settings.json"
    write(settings, foreign_settings())
    outputs = [cli(settings)[1], cli(settings, "--apply")[1], cli(settings, "--uninstall")[1],
               cli(settings, "--uninstall", "--apply")[1]]
    for out in outputs:
        for canary in (SECRET, URL_CANARY, "zz-canary", "ZZ-CANARY-USER", "coord.py", "notify", "status.sh",
                       "permissions", "Bash(ls", "{", "cc_hook"):
            assert canary.encode() not in out, canary
    bad = tmp_path / "bad.json"
    bad.write_bytes(b'{"hooks": {"x": "' + SECRET.encode() + b'"')
    code, out = cli(bad, "--apply")
    assert code == 1 and SECRET.encode() not in out and b"not valid JSON" in out


def test_installer_preserves_foreign_hooks(inst, tmp_path):
    settings = tmp_path / "settings.json"
    before = foreign_settings()
    write(settings, before)
    counts = inst.run(settings, apply=True, uninstall=False)
    assert counts["added"] == 3
    after = json.loads(settings.read_bytes())
    assert list(after) == list(before)  # key order kept
    for key in ("permissions", "statusLine", "enabledPlugins"):
        assert after[key] == before[key]
    assert after["hooks"]["PreToolUse"] == before["hooks"]["PreToolUse"]
    assert after["hooks"]["Stop"][0] == before["hooks"]["Stop"][0]
    for event in ("Stop", "SubagentStop", "SessionEnd"):
        entry = after["hooks"][event][-1]
        assert entry == {"matcher": "", "hooks": [{"type": "command", "command": ours(inst), "timeout": 10}]}
    raw = settings.read_bytes()
    assert raw.endswith(b"}\n") and b'\n  "hooks": {' in raw  # UTF-8, 2-space indent, trailing newline


def test_hook_command_per_platform(inst):
    assert inst.hook_command("win32").startswith('python "') and inst.hook_command("linux").startswith('python3 "')
    assert inst.hook_command("win32").endswith('/producers/claude_code/cc_hook.py"')


def test_installer_idempotent_backup_uninstall(inst, tmp_path):
    settings = tmp_path / "settings.json"
    write(settings, foreign_settings())
    inst.run(settings, apply=True, uninstall=False)
    backups = sorted(tmp_path.glob("settings.json.bak-ti-*"))
    assert len(backups) == 1 and json.loads(backups[0].read_bytes()) == foreign_settings()
    once = settings.read_bytes()
    counts = inst.run(settings, apply=True, uninstall=False)
    assert counts["added"] == 0 and counts["already_present"] == 3 and settings.read_bytes() == once
    counts = inst.run(settings, apply=True, uninstall=True)
    assert counts["removed"] == 3
    assert json.loads(settings.read_bytes()) == foreign_settings()
    # invalid JSON -> exit 1, nothing written
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{nope")
    with pytest.raises(inst.InstallError):
        inst.run(bad, apply=True, uninstall=False)
    assert bad.read_bytes() == b"{nope" and not list(tmp_path.glob("bad.json.bak-ti-*"))
    # missing file -> created with only our hooks
    fresh = tmp_path / "new" / "settings.json"
    inst.run(fresh, apply=True, uninstall=False)
    assert set(json.loads(fresh.read_bytes())) == {"hooks"}
    assert set(json.loads(fresh.read_bytes())["hooks"]) == {"Stop", "SubagentStop", "SessionEnd"}


def test_installer_exact_ownership(inst, tmp_path):
    settings = tmp_path / "settings.json"
    mine = ours(inst)
    other_path = 'python "C:/elsewhere/producers/claude_code/cc_hook.py"'
    other_interp = mine.replace("python", "py", 1) if mine.startswith("python ") else "python" + mine[len("python3"):]
    data = {"hooks": {
        "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": other_path},
                                           {"type": "command", "command": other_interp}]},
                 {"matcher": "", "hooks": [{"type": "command", "command": mine, "timeout": 10},
                                           {"type": "command", "command": "foreign-in-mixed"}]}],
        "SubagentStop": [{"matcher": "", "hooks": [{"type": "command", "command": mine, "timeout": 10}]}],
    }}
    write(settings, data)
    counts = inst.run(settings, apply=True, uninstall=True)
    assert counts["removed"] == 2
    after = json.loads(settings.read_bytes())["hooks"]
    commands = [h["command"] for g in after["Stop"] for h in g["hooks"]]
    assert commands == [other_path, other_interp, "foreign-in-mixed"]  # mixed group kept, only our object left
    assert len(after["Stop"]) == 2
    assert "SubagentStop" not in after  # became empty and was ours


def test_installer_aborts_on_concurrent_change(inst, tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    write(settings, foreign_settings())
    other = b'{"hooks": {}, "changedBy": "other-session"}\n'
    monkeypatch.setattr(inst, "_before_write", lambda: settings.write_bytes(other))
    with pytest.raises(inst.InstallError) as err:
        inst.run(settings, apply=True, uninstall=False)
    assert "changed since it was read" in str(err.value)
    assert settings.read_bytes() == other
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    # normal apply goes through a same-directory temp file + os.replace
    monkeypatch.setattr(inst, "_before_write", None)
    seen = []
    real_replace = inst.os.replace
    monkeypatch.setattr(inst.os, "replace", lambda a, b: (seen.append((Path(a).parent, Path(b))), real_replace(a, b)))
    inst.run(settings, apply=True, uninstall=False)
    assert seen and seen[0][0] == tmp_path and seen[0][1] == settings


def test_installer_takes_possibleskills_lock(inst, tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    write(settings, foreign_settings())
    monkeypatch.setattr(inst, "LOCK_WAIT_S", 0.3)
    with inst.settings_lock(settings):  # another installer holds the OS lock (a second handle cannot take it)
        with pytest.raises(inst.InstallError) as err:
            inst.run(settings, apply=True, uninstall=False)
    assert "lock is held" in str(err.value)
    assert json.loads(settings.read_bytes()) == foreign_settings()
    assert (tmp_path / "settings.json.lock-possibleskills").exists()
    inst.run(settings, apply=True, uninstall=False)  # released: the next apply goes through
    assert json.loads(settings.read_bytes())["hooks"]["SessionEnd"]


def test_installer_backup_never_overwritten_in_same_second(inst, tmp_path, monkeypatch):
    """code-r1 p3-F3: install then uninstall within one UTC second keeps the pre-install backup."""
    import datetime as dt

    class FixedClock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2026, 10, 2, 12, 0, 0, tzinfo=dt.timezone.utc)

    monkeypatch.setattr(inst, "datetime", FixedClock)
    settings = tmp_path / "settings.json"
    write(settings, foreign_settings())
    inst.run(settings, apply=True, uninstall=False)
    inst.run(settings, apply=True, uninstall=True)
    backups = sorted(tmp_path.glob("settings.json.bak-ti-*"))
    assert len(backups) == 2
    assert json.loads(backups[0].read_bytes()) == foreign_settings()  # the pre-install copy survives
    assert json.loads(backups[1].read_bytes())["hooks"]["SessionEnd"]


def test_installer_removes_partial_backup_after_write_failure(inst, tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    raw = write(settings, foreign_settings())
    real_open = open

    class FailingBackup:
        def __init__(self, path, mode):
            self.fh = real_open(path, mode)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.fh.close()

        def write(self, payload):
            raise OSError("synthetic backup failure")

    def failing_open(path, mode="r", *args, **kwargs):
        if ".bak-ti-" in str(path):
            return FailingBackup(path, mode)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", failing_open)
    with pytest.raises(inst.InstallError, match="backup could not be written; nothing changed"):
        inst.run(settings, apply=True, uninstall=False)
    assert settings.read_bytes() == raw
    assert not list(tmp_path.glob("settings.json.bak-ti-*"))


@pytest.mark.parametrize("uninstall", [False, True])
def test_installer_rejects_shell_metacharacters_in_hook_path(inst, tmp_path, monkeypatch, uninstall):
    settings = tmp_path / "settings.json"
    raw = write(settings, foreign_settings())
    monkeypatch.setattr(inst, "HOOK_PATH", tmp_path / "$HOME" / "cc_hook.py")
    with pytest.raises(inst.InstallError, match="hook path contains shell metacharacters; nothing changed"):
        inst.run(settings, apply=True, uninstall=uninstall)
    assert settings.read_bytes() == raw
    assert not list(tmp_path.glob("settings.json.bak-ti-*"))
