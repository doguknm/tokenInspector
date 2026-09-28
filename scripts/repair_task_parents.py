"""Re-link cross-project child tasks whose parent_task_ref was built from the child's own project.

Before O9 (J6), `task_store._upsert_task` hashed the parent turn with the CHILD's project, so a child in
project B whose parent turn lives in project A got a `parent_task_ref` that matches no row (dangling),
and a provisional `root_task_ref` equal to that wrong ref. This script re-derives the parent by hash
matching: for a dangling child in project P, the parent is the one row in another project whose
(session_id, turn_id) hashes to the dangling ref under P (the pre-fix formula).

Standalone: stdlib only, no imports from the application (same style as purge_task_prompts.py).
Prints counts only, never refs, names, sessions or paths.

    python scripts/repair_task_parents.py --db PATH [--apply] [--backup-dir DIR]

Dry-run is the default. --apply takes a verified online backup first, then recomputes the plan inside
one BEGIN IMMEDIATE transaction (the dry-run analysis is never applied) and checks postconditions
before committing. Idempotent: a second run finds nothing left to relink.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

MAX_DEPTH = 64
CLASSES = ("ambiguous", "unmatched", "cyclic", "depth_exceeded", "unresolved_ancestor")


def task_ref(project_name: str, session_id: str | None, turn_id: str) -> str:
    """Same formula as task_store.task_ref (a test pins the equality)."""
    raw = "\x1f".join((project_name, session_id or "", turn_id))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class RepairError(RuntimeError):
    """Fixed-text failure; never carries refs or names."""


@dataclass
class Plan:
    dangling: int = 0
    relinkable: int = 0
    refused: dict[str, int] = field(default_factory=lambda: {name: 0 for name in CLASSES})
    relinks: dict[str, str] = field(default_factory=dict)  # child id -> new parent id
    roots: dict[str, str] = field(default_factory=dict)  # row id -> new root_task_ref

    @property
    def relinked(self) -> int:
        return len(self.relinks)

    def line(self, mode: str, roots_updated: int | None = None) -> str:
        counts = " ".join(f"{name}={self.refused[name]}" for name in CLASSES)
        roots = len(self.roots) if roots_updated is None else roots_updated
        return (f"dangling={self.dangling} relinkable={self.relinkable} {counts} "
                f"relinked={self.relinked} roots_updated={roots} mode={mode}")


def _rows(conn: sqlite3.Connection) -> dict[str, tuple]:
    return {
        row[0]: row[1:]
        for row in conn.execute("SELECT id, project_name, session_id, turn_id, parent_task_ref, root_task_ref FROM tasks")
    }


def plan_repair(conn: sqlite3.Connection) -> Plan:
    """Read-only: the proposed graph is computed completely before any write (order-independent)."""
    rows = _rows(conn)
    plan = Plan()
    dangling = sorted(i for i, r in rows.items() if r[3] is not None and r[3] not in rows)
    plan.dangling = len(dangling)
    by_project: dict[str, dict[str, list[str]]] = {}
    candidates: dict[str, str] = {}
    for child in dangling:
        project, old_parent = rows[child][0], rows[child][3]
        if project not in by_project:
            index: dict[str, list[str]] = {}
            for other, (other_project, session, turn, _p, _r) in rows.items():
                if other_project != project:
                    index.setdefault(task_ref(project, session, turn), []).append(other)
            by_project[project] = index
        matches = by_project[project].get(old_parent, [])
        if len(matches) > 1:
            plan.refused["ambiguous"] += 1
        elif not matches:
            plan.refused["unmatched"] += 1
        elif matches[0] == child:
            plan.refused["cyclic"] += 1
        else:
            candidates[child] = matches[0]
    plan.relinkable = len(candidates)

    def effective_parent(node: str) -> str | None:
        return candidates.get(node, rows[node][3])

    accepted_roots: dict[str, str] = {}
    for child in sorted(candidates):
        visited, node, depth, outcome = {child}, candidates[child], 1, None
        while outcome is None:
            if node in visited:
                outcome = "cyclic"
            elif depth > MAX_DEPTH:
                outcome = "depth_exceeded"
            elif node not in rows:
                outcome = "unresolved_ancestor"
            else:
                parent = effective_parent(node)
                if parent is None:
                    outcome = rows[node][4] or node  # the _upsert_task root rule
                else:
                    visited.add(node)
                    node, depth = parent, depth + 1
        if outcome in CLASSES:
            plan.refused[outcome] += 1
        else:
            accepted_roots[child] = outcome
    plan.relinks = {child: candidates[child] for child in accepted_roots}
    refused = set(candidates) - set(accepted_roots)

    # Re-root every row that used an accepted child's old wrong ref as its (provisional) root.
    old_to_root = {rows[child][3]: root for child, root in accepted_roots.items()}
    for row_id, (_proj, _s, _t, _parent, root) in rows.items():
        if row_id not in refused and root in old_to_root and old_to_root[root] != root:
            plan.roots[row_id] = old_to_root[root]
    for child, root in accepted_roots.items():
        if rows[child][4] != root:
            plan.roots[child] = root
    graph = {i: (plan.relinks.get(i, r[3]), plan.roots.get(i, r[4])) for i, r in rows.items()}
    _check(plan, graph)
    return plan


def _check(plan: Plan, graph: dict[str, tuple]) -> None:
    """Postconditions over {id: (parent_task_ref, root_task_ref)}: the proposed or the applied graph."""
    if plan.dangling != plan.relinked + sum(plan.refused.values()):
        raise RepairError("postcondition failed: counts do not add up")
    for child, new_parent in plan.relinks.items():
        if new_parent not in graph or graph[child][0] != new_parent:
            raise RepairError("postcondition failed: relinked parent missing")
        root = graph[child][1]
        if root not in graph or graph[root][0] is not None:
            raise RepairError("postcondition failed: root has a parent")
        seen, node = set(), child
        while node is not None:
            if node in seen or len(seen) > MAX_DEPTH or node not in graph:
                raise RepairError("postcondition failed: cycle or dangling ancestor in the repaired graph")
            seen.add(node)
            node = graph[node][0]
    for row_id, root in plan.roots.items():
        if graph[row_id][1] != root:
            raise RepairError("postcondition failed: root not applied")


def _apply_plan(conn: sqlite3.Connection, plan: Plan, now: str) -> int:
    for child, new_parent in sorted(plan.relinks.items()):
        _update(conn, "UPDATE tasks SET parent_task_ref = ?, updated_at = ? WHERE id = ?", (new_parent, now, child))
    changed = 0
    for row_id, root in sorted(plan.roots.items()):
        changed += _update(conn, "UPDATE tasks SET root_task_ref = ?, updated_at = ? WHERE id = ? "
                                 "AND COALESCE(root_task_ref, '') <> ?", (root, now, row_id, root))
    return changed


def _update(conn: sqlite3.Connection, sql: str, params: tuple) -> int:
    return conn.execute(sql, params).rowcount


def _counts(conn: sqlite3.Connection) -> tuple[int, int]:
    return (conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM token_events").fetchone()[0])


def backup(db: Path, backup_dir: Path | None) -> Path:
    """Online backup verified against its own snapshot: before <= backup <= after (no path deletes rows)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = (backup_dir or db.parent) / f"{db.name}.bak-repair-{stamp}"
    with closing(sqlite3.connect(db)) as src:
        src.execute("PRAGMA busy_timeout=5000")
        before = _counts(src)
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
        after = _counts(src)
    verify_backup(target, before, after)
    return target


def verify_backup(target: Path, before: tuple[int, int], after: tuple[int, int]) -> None:
    with closing(sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)) as bak:
        if bak.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RepairError("backup verification failed: integrity_check")
        got = _counts(bak)
    if not all(b <= g <= a for b, g, a in zip(before, got, after)):
        raise RepairError("backup verification failed: row counts")


def _before_apply_transaction() -> None:
    """Test seam between the analysis and the write transaction (no-op)."""


def run(db: Path, *, apply: bool = False, backup_dir: Path | None = None) -> int:
    if not db.is_file():
        print("repair_task_parents: database file not found", file=sys.stderr)
        return 1
    try:
        with closing(sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)) as ro:
            analysis = plan_repair(ro)
        if not apply:
            print(analysis.line("dry-run"))
            return 0
        backup(db, backup_dir)
        _before_apply_transaction()
        with closing(sqlite3.connect(db, isolation_level=None)) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("BEGIN IMMEDIATE")
            try:
                plan = plan_repair(conn)  # recomputed under the write lock; the analysis is never applied
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                roots_updated = _apply_plan(conn, plan, now)
                _check(plan, {i: (r[3], r[4]) for i, r in _rows(conn).items()})  # the applied graph
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        print(plan.line("apply", roots_updated))
        return 0
    except (RepairError, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, RepairError) else "sqlite error"
        print(f"repair_task_parents: {message}; nothing was changed", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Re-link dangling cross-project task parents (dry-run default).")
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="write the changes (after a verified backup)")
    parser.add_argument("--backup-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    return run(args.db, apply=args.apply, backup_dir=args.backup_dir)


if __name__ == "__main__":
    sys.exit(main())
