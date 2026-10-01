"""O9 J7 / AC1.8: scripts/repair_task_parents.py (dry-run default, verified backup, graph plan, atomic apply)."""

import hashlib
import random
import re
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

import database
import task_store
from scripts import repair_task_parents as repair

REF = task_store.task_ref
HEX32 = re.compile(r"[0-9a-f]{32}")
COLS = ("id, project_name, session_id, turn_id, parent_task_ref, root_task_ref, hierarchy_status, source, "
        "first_seen_at, last_seen_at, prompt_truncated, created_at, updated_at")


def task(project, session, turn, parent=None, root=None):
    """A tasks row as the pre-fix code wrote it: parent/root refs given explicitly."""
    return (REF(project, session, turn), project, session, turn, parent, root, "child" if parent else "root")


def wrong(child_project, parent_session, parent_turn):
    """The pre-fix parent ref: hashed with the child's own project."""
    return REF(child_project, parent_session, parent_turn)


async def build(path: Path, rows, shuffle_seed=None) -> Path:
    await database.init_db(str(path))
    rows = list(rows)
    if shuffle_seed is not None:
        random.Random(shuffle_seed).shuffle(rows)
    with closing(sqlite3.connect(path)) as conn:
        conn.executemany(f"INSERT INTO tasks ({COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, 'ingest', 'x', 'x', 0, 'x', 'x')", rows)
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return path


def state(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as conn:
        return {r[0]: r[1:] for r in conn.execute("SELECT id, parent_task_ref, root_task_ref, updated_at FROM tasks")}


def main_rows():
    w1, w2, wx = wrong("b", "ps1", "pt1"), wrong("b", "ps2", "pt2"), wrong("b", "psx", "ptx")
    c1 = task("b", "cs1", "ct1", w1, w1)
    return [
        task("a", "ps1", "pt1"), task("a", "ps2", "pt2"),
        c1, task("b", "cs2", "ct2", w2, w2), task("b", "cs3", "ct3", w1, w1),
        task("b", "gs", "gt", c1[0], w1),  # grandchild in b: its parent C1 exists, its root is C1's wrong ref
        task("a", "psx", "ptx"), task("c", "psx", "ptx"), task("b", "xs", "xt", wx, wx),  # ambiguous
        task("b", "us", "ut", wrong("b", "nope", "nope"), wrong("b", "nope", "nope")),  # unmatched
    ]


DRY = ("dangling=5 relinkable=3 ambiguous=1 unmatched=1 cyclic=0 depth_exceeded=0 unresolved_ancestor=0 "
       "relinked=3 roots_updated=4 mode=dry-run")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_task_ref_formula_matches_task_store():
    assert repair.task_ref("a", "s", "t") == task_store.task_ref("a", "s", "t")
    assert repair.task_ref("a", None, "t") == task_store.task_ref("a", None, "t")


async def test_repair_dry_run_by_default(tmp_path, capsys):
    path = await build(tmp_path / "db.sqlite", main_rows())
    before = _file_hash(path)
    assert repair.main(["--db", str(path)]) == 0
    out = capsys.readouterr().out.strip()
    assert out == DRY
    assert _file_hash(path) == before and not list(tmp_path.glob("*.bak-repair-*"))


async def test_repair_apply_relinks_and_reroots(tmp_path, capsys):
    path = await build(tmp_path / "db.sqlite", main_rows())
    before = state(path)
    assert repair.main(["--db", str(path), "--apply"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == DRY.replace("mode=dry-run", "mode=apply")
    assert not HEX32.search(out) and "hermes" not in out and str(tmp_path) not in out
    after = state(path)
    p1, p2 = REF("a", "ps1", "pt1"), REF("a", "ps2", "pt2")
    assert after[REF("b", "cs1", "ct1")][:2] == (p1, p1)
    assert after[REF("b", "cs3", "ct3")][:2] == (p1, p1)
    assert after[REF("b", "cs2", "ct2")][:2] == (p2, p2)
    assert after[REF("b", "gs", "gt")][:2] == (REF("b", "cs1", "ct1"), p1)  # descendant re-rooted
    assert after[REF("b", "cs1", "ct1")][2] != "x"  # updated_at set
    for untouched in (REF("b", "xs", "xt"), REF("b", "us", "ut"), p1):
        assert after[untouched] == before[untouched]


async def test_repair_backs_up_before_apply(tmp_path, monkeypatch, capsys):
    path = await build(tmp_path / "db.sqlite", main_rows())
    assert repair.main(["--db", str(path), "--apply", "--backup-dir", str(tmp_path)]) == 0
    (backup,) = [p for p in tmp_path.glob("db.sqlite.bak-repair-*") if not p.name.endswith(("-wal", "-shm"))]
    with closing(sqlite3.connect(backup)) as bak:
        assert bak.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert bak.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == len(main_rows())
        assert bak.execute("SELECT COUNT(*) FROM tasks WHERE parent_task_ref = ?", (REF("a", "ps1", "pt1"),)
                           ).fetchone()[0] == 0  # taken before the relinks

    fresh = await build(tmp_path / "second.sqlite", main_rows())
    before = state(fresh)

    def broken(*_args):
        raise repair.RepairError("backup verification failed: simulated")

    monkeypatch.setattr(repair, "verify_backup", broken)
    assert repair.main(["--db", str(fresh), "--apply"]) == 1
    assert state(fresh) == before
    assert "nothing was changed" in capsys.readouterr().err


async def test_repair_idempotent(tmp_path, capsys):
    path = await build(tmp_path / "db.sqlite", main_rows())
    assert repair.main(["--db", str(path), "--apply"]) == 0
    first = state(path)
    capsys.readouterr()
    assert repair.main(["--db", str(path), "--apply"]) == 0
    assert capsys.readouterr().out.strip() == (
        "dangling=2 relinkable=0 ambiguous=1 unmatched=1 cyclic=0 depth_exceeded=0 unresolved_ancestor=0 "
        "relinked=0 roots_updated=0 mode=apply")
    assert state(path) == first


def graph_rows():
    rows = [task("a", "rs", "rt")]
    # (a) K (b) -> M (c) -> R (a): M is itself being relinked; K's root follows the proposed graph
    m = task("c", "ms", "mt", wrong("c", "rs", "rt"), wrong("c", "rs", "rt"))
    k = task("b", "ks", "kt", wrong("b", "ms", "mt"), wrong("b", "ms", "mt"))
    rows += [m, k]
    # (b) X1 (b) and X2 (c) would become each other's parents
    rows += [task("b", "x1s", "x1t", wrong("b", "x2s", "x2t"), wrong("b", "x2s", "x2t")),
             task("c", "x2s", "x2t", wrong("c", "x1s", "x1t"), wrong("c", "x1s", "x1t"))]
    # (d) D (b) -> chain of 66 rows in project a
    rows.append(task("a", "n", "0"))
    rows += [task("a", "n", str(i), REF("a", "n", str(i - 1)), REF("a", "n", "0")) for i in range(1, 66)]
    rows.append(task("b", "ds", "dt", wrong("b", "n", "65"), wrong("b", "n", "65")))
    # (e) E (b) -> Q (a) whose own parent ref exists nowhere
    rows.append(task("a", "qs", "qt", "f" * 32, "f" * 32))
    rows.append(task("b", "es", "et", wrong("b", "qs", "qt"), wrong("b", "qs", "qt")))
    # (c) a row whose parent ref is itself is not dangling (the self-match guard cannot be reached)
    rows.append((REF("d", "ss", "st"), "d", "ss", "st", REF("d", "ss", "st"), None, "child"))
    return rows


GRAPH = ("dangling=7 relinkable=6 ambiguous=0 unmatched=1 cyclic=2 depth_exceeded=1 unresolved_ancestor=1 "
         "relinked=2 roots_updated=2 mode={}")


async def test_repair_graph_cases(tmp_path, capsys):
    results = []
    for seed in (None, 7, 11):
        path = await build(tmp_path / f"g{seed}.sqlite", graph_rows(), shuffle_seed=seed)
        assert repair.main(["--db", str(path)]) == 0
        assert capsys.readouterr().out.strip() == GRAPH.format("dry-run")
        before = state(path)
        assert repair.main(["--db", str(path), "--apply"]) == 0
        assert capsys.readouterr().out.strip() == GRAPH.format("apply")
        after = state(path)
        r = REF("a", "rs", "rt")
        assert after[REF("c", "ms", "mt")][:2] == (r, r)
        assert after[REF("b", "ks", "kt")][:2] == (REF("c", "ms", "mt"), r)  # (a) order-independent root
        for refused in (REF("b", "x1s", "x1t"), REF("c", "x2s", "x2t"), REF("b", "ds", "dt"), REF("b", "es", "et"),
                        REF("a", "qs", "qt"), REF("d", "ss", "st")):
            assert after[refused] == before[refused]
        results.append({k: v[:2] for k, v in after.items()})
    assert results[0] == results[1] == results[2]


async def test_repair_revalidates_under_write_lock(tmp_path, monkeypatch, capsys):
    path = await build(tmp_path / "db.sqlite", main_rows())

    def change_between_analysis_and_apply():
        with closing(sqlite3.connect(path)) as conn:
            # C2's parent now matches two rows (ambiguous); U's parent appears (relinkable)
            conn.execute(f"INSERT INTO tasks ({COLS}) VALUES (?, 'c', 'ps2', 'pt2', NULL, NULL, 'root', 'ingest', "
                         "'x', 'x', 0, 'x', 'x')", (REF("c", "ps2", "pt2"),))
            conn.execute(f"INSERT INTO tasks ({COLS}) VALUES (?, 'a', 'nope', 'nope', NULL, NULL, 'root', 'ingest', "
                         "'x', 'x', 0, 'x', 'x')", (REF("a", "nope", "nope"),))
            conn.commit()

    monkeypatch.setattr(repair, "_before_apply_transaction", change_between_analysis_and_apply)
    assert repair.main(["--db", str(path), "--apply"]) == 0
    assert capsys.readouterr().out.strip() == (
        "dangling=5 relinkable=3 ambiguous=2 unmatched=0 cyclic=0 depth_exceeded=0 unresolved_ancestor=0 "
        "relinked=3 roots_updated=4 mode=apply")
    after = state(path)
    assert after[REF("b", "cs2", "ct2")][0] == wrong("b", "ps2", "pt2")  # now ambiguous: untouched
    assert after[REF("b", "us", "ut")][:2] == (REF("a", "nope", "nope"),) * 2


async def test_repair_backup_verified_during_ingest(tmp_path, monkeypatch):
    path = await build(tmp_path / "db.sqlite", main_rows())
    real_counts, calls = repair._counts, []

    def counts_with_ingest(conn):
        calls.append(1)
        if len(calls) == 2:  # the source's after-count: rows were ingested after the backup finished
            with closing(sqlite3.connect(path)) as writer:
                for n in range(3):
                    writer.execute(f"INSERT INTO tasks ({COLS}) VALUES (?, 'z', 's', ?, NULL, NULL, 'root', 'ingest', "
                                   "'x', 'x', 0, 'x', 'x')", (REF("z", "s", str(n)), str(n)))
                writer.execute("INSERT INTO token_events (id, project_name, recorded_at, event_type, model, "
                               "prompt_tokens, completion_tokens, cache_read_tokens, cache_creation_tokens, "
                               "reasoning_tokens, status, attempt, retry_count, cost_status) VALUES ('ev', 'z', 'x', "
                               "'llm_request', 'm', 0, 0, 0, 0, 0, 'success', 1, 0, 'unpriced')")
                writer.commit()
        return real_counts(conn)

    monkeypatch.setattr(repair, "_counts", counts_with_ingest)
    assert repair.main(["--db", str(path), "--apply"]) == 0  # before <= backup <= after: accepted

    fresh = await build(tmp_path / "second.sqlite", main_rows())
    before = state(fresh)
    calls.clear()

    def inflated_before(conn):
        calls.append(1)
        tasks, events = real_counts(conn)
        return (tasks + 100, events) if len(calls) == 1 else (tasks, events)

    monkeypatch.setattr(repair, "_counts", inflated_before)
    assert repair.main(["--db", str(fresh), "--apply"]) == 1  # backup has fewer rows than the before-count
    assert state(fresh) == before


async def test_repair_backup_accepts_real_concurrent_writer(tmp_path):
    path = await build(tmp_path / "db.sqlite", main_rows())
    stop = threading.Event()

    def writer():
        with closing(sqlite3.connect(path, timeout=5)) as conn:
            n = 0
            while not stop.is_set():
                conn.execute(f"INSERT INTO tasks ({COLS}) VALUES (?, 'z', 's', ?, NULL, NULL, 'root', 'ingest', "
                             "'x', 'x', 0, 'x', 'x')", (REF("z", "w", str(n)), f"w{n}"))
                conn.commit()
                n += 1
                # Leave the write lock free between commits: a loop that re-takes it at once can starve the
                # repair's BEGIN IMMEDIATE past its busy timeout (CLAUDE.md RP 19).
                time.sleep(0.001)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert repair.main(["--db", str(path), "--apply"]) == 0
    finally:
        stop.set()
        thread.join(timeout=10)


async def test_repair_failure_is_atomic(tmp_path, monkeypatch, capsys):
    path = await build(tmp_path / "db.sqlite", main_rows())
    before = state(path)
    real_update, calls = repair._update, []

    def failing_update(conn, sql, params):
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("injected")
        return real_update(conn, sql, params)

    monkeypatch.setattr(repair, "_update", failing_update)
    assert repair.main(["--db", str(path), "--apply"]) == 1
    assert state(path) == before
    monkeypatch.setattr(repair, "_update", real_update)

    # a failed postcondition on the applied graph (3rd check: analysis, in-transaction plan, applied graph)
    real_check, checks = repair._check, []

    def failing_check(plan, graph):
        checks.append(1)
        if len(checks) == 3:
            raise repair.RepairError("postcondition failed: simulated")
        return real_check(plan, graph)

    monkeypatch.setattr(repair, "_check", failing_check)
    assert repair.main(["--db", str(path), "--apply"]) == 1
    assert len(checks) == 3 and state(path) == before
    assert "hermes" not in capsys.readouterr().err
