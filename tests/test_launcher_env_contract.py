"""O9 J8 / AC1.2 (B-F11): synthetic execution of the job env contract in every hermes.sh launcher copy.

Each launcher runs from bash with stub ssh/scp (tests/fixtures/launcher_stub) that execute the "remote"
command locally, so the real runner text is written, substituted and started; a stub agent records the
TOKEN_INSPECTOR_* environment it received. Nothing reaches hermes. Copies owned by other sessions (S2, S3)
run with the O9 patch applied to a temp copy until their owner applies it (then the real file runs).
The launcher files live outside this repo (Windows workspace); where they are absent the test skips.
"""

import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from routes.events import JOB_REF_RE

REPO = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("O9_LAUNCHER_ROOT", REPO.parent))  # the mutation check points this at a mutated copy
STUBS = Path(__file__).parent / "fixtures" / "launcher_stub"
PATCHES = REPO / "Plans" / "o9-job-correlation-cc-producer" / "shared-patches"
SKILL = Path(os.environ.get("O9_HERMES_SKILL", Path.home().parent / "Bentego_Admin" / ".claude" / "commands" / "hermes.md"))
PATH_CANARY = "C:\\Users\\ZZ-CANARY-USER\\x"
COPIES = {
    "S1-AIFromScratch": ("AIFromScratch", None),
    "S2-PEGADocRag": ("PEGADocRag", "S2-PEGADocRag-hermes.sh.patch"),
    "S3-PEGADocRagAgent": ("PEGADocRagAgent", "S3-PEGADocRagAgent-hermes.sh.patch"),
}


def _bash() -> str | None:
    if sys.platform != "win32":
        return shutil.which("bash")
    git = shutil.which("git")  # Git for Windows' bash, never WSL's System32\bash.exe
    if git:
        for base in Path(git).parents:
            for candidate in (base / "bin" / "bash.exe", base / "usr" / "bin" / "bash.exe"):
                if candidate.is_file():
                    return str(candidate)
    return None


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _sh(script: str, env=None, timeout=60) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, timeout=timeout, env=env)


def _posix(path: Path) -> str:
    if sys.platform != "win32":
        return str(path)
    return _sh(f"cygpath -u '{path}'").stdout.strip()


def _native(posix: str) -> Path:
    if sys.platform != "win32":
        return Path(posix)
    return Path(_sh(f"cygpath -w '{posix}'").stdout.strip())


@pytest.fixture
def work():
    """A space-free POSIX work dir (the runner line does not quote paths) holding LF copies of the stubs."""
    posix = _sh("mktemp -d /tmp/o9-launch.XXXXXX").stdout.strip()
    native = _native(posix)
    (native / "bin").mkdir()
    for stub in ("ssh", "scp", "setsid", "agent"):
        target = native / "bin" / stub
        target.write_text((STUBS / stub).read_text(encoding="utf-8").replace("\r\n", "\n"), encoding="utf-8",
                          newline="\n")
        target.chmod(0o755)
    (native / "repo").mkdir()
    (native / "prompt.txt").write_text("zz synthetic prompt\n", encoding="utf-8")
    yield posix, native
    _sh(f"rm -rf '{posix}'")
    shutil.rmtree(native, ignore_errors=True)


def _launcher(copy: str, native: Path) -> Path:
    repo_name, patch = COPIES[copy]
    source = ROOT / repo_name / "scripts" / "hermes.sh"
    if not source.is_file():
        pytest.skip(f"{copy}: launcher not present on this machine")
    text = source.read_bytes()
    if b"TOKEN_INSPECTOR_JOB_REF" in text:
        return source  # the O9 change is in the real file
    if patch is None:
        pytest.fail(f"{copy}: the O9 change is missing from the TI-owned launcher")
    target = native / f"{copy}.sh"
    target.write_bytes(text)
    applied = _sh(f"cd '{_posix(native)}' && patch -s -o '{copy}.patched.sh' '{copy}.sh' < '{_posix(PATCHES / patch)}'")
    if applied.returncode != 0:
        pytest.fail("launcher O9 patch does not apply")
    return native / f"{copy}.patched.sh"


def _run(copy, work, command, work_type=None, attempt=None):
    posix, native = work
    launcher = _launcher(copy, native)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TOKEN_INSPECTOR_", "HERMES_"))}
    tag = f"o9t{uuid.uuid4().hex[:8]}"
    env.update({
        "PATH": f"{native / 'bin'}{os.pathsep}{env.get('PATH', '')}",
        "HERMES_SSH": f"{posix}/bin/ssh", "HERMES_SCP": f"{posix}/bin/scp", "HERMES_HOST": "stubhost",
        "HERMES_AGENT": f"{posix}/bin/agent", "HERMES_CLAUDE": f"{posix}/bin/agent",
        "HERMES_REPO": f"{posix}/repo", "HERMES_DEVIR_REPO": f"{posix}/repo", "HERMES_BARE": f"{posix}/repo",
        "HERMES_TAG": tag, "STUB_RECORD": f"{posix}/record.txt",
    })
    if work_type is not None:
        env["HERMES_WORK_TYPE"] = work_type
    if attempt is not None:
        env["HERMES_JOB_ATTEMPT"] = attempt
    # prepend the stub dir inside bash too (Git Bash rebuilds PATH from the Windows value)
    script = f"export PATH='{posix}/bin':\"$PATH\"; cd '{posix}' && bash '{_posix(launcher)}' {command} '{posix}/prompt.txt'"
    run = _sh(script, env=env, timeout=90)
    assert run.returncode == 0, run.stderr[-600:]
    record = native / "record.txt"
    for _ in range(100):
        if record.is_file():
            break
        subprocess.run([BASH, "-c", "sleep 0.1"], timeout=5)
    assert record.is_file(), "the stub agent never ran"
    lines = record.read_text(encoding="utf-8").splitlines()
    got = dict(line.split("=", 1) for line in lines)
    match = re.search(r"job=(\S+)", run.stdout)
    assert match, "launch line must carry job=<id>"
    for created in Path("/tmp").glob(f"hermes_*{tag}*"):
        if created.is_dir():
            shutil.rmtree(created, ignore_errors=True)
        else:
            created.unlink(missing_ok=True)
    return got, match.group(1), run.stdout


@pytest.mark.parametrize("copy", list(COPIES))
def test_launcher_env_contract_send(copy, work):
    got, job, _ = _run(copy, work, "send", work_type="review", attempt="2")
    assert JOB_REF_RE.fullmatch(job) and not job.startswith("devir-")
    assert (got["ARG1"], got["ARGC"]) == ("-z", "2")  # no model/provider flag was added
    assert {k: v for k, v in got.items() if k.startswith("TOKEN_INSPECTOR_")} == {
        "TOKEN_INSPECTOR_JOB_REF": job, "TOKEN_INSPECTOR_WORK_TYPE": "review", "TOKEN_INSPECTOR_JOB_ATTEMPT": "2"}


@pytest.mark.parametrize("copy", list(COPIES))
@pytest.mark.parametrize(("work_type", "attempt"), [("Bad Value", "0"), (PATH_CANARY, "1.5"), ("", "10000")])
def test_launcher_drops_invalid_work_type_and_attempt(copy, work, work_type, attempt):
    got, job, stdout = _run(copy, work, "send", work_type=work_type, attempt=attempt)
    assert {k: v for k, v in got.items() if k.startswith("TOKEN_INSPECTOR_")} == {"TOKEN_INSPECTOR_JOB_REF": job}
    assert "ZZ-CANARY" not in stdout


def test_launcher_devir_baslat_sets_devir_job(work):
    got, job, _ = _run("S3-PEGADocRagAgent", work, "devir-baslat", work_type="review", attempt="3")
    assert JOB_REF_RE.fullmatch(job) and job.startswith("devir-")
    assert got["ARG1"] == "-p"
    assert {k: v for k, v in got.items() if k.startswith("TOKEN_INSPECTOR_")} == {
        "TOKEN_INSPECTOR_JOB_REF": job, "TOKEN_INSPECTOR_WORK_TYPE": "devir", "TOKEN_INSPECTOR_JOB_ATTEMPT": "3"}


def test_hermes_skill_passes_work_type():
    if not SKILL.is_file():
        pytest.skip("the /hermes skill file is not on this machine")
    text = SKILL.read_text(encoding="utf-8")
    assert "HERMES_WORK_TYPE=<mode> bash scripts/hermes.sh send <prompt-file>" in text
    for mode in ("brainstorm", "review", "code"):
        assert f"HERMES_WORK_TYPE={mode}" in text
    assert "never put anything else in it" in text
