"""Schema rollback runner: the only supported way to run scripts/rollback_v<N>.sql.

Standalone: stdlib only, no imports from the application. Run with the service stopped
(Plans/o9-job-correlation-cc-producer/database.md, "Schema rollback runner"). Prints versions only.

    python scripts/rollback_schema.py --db <path> --to <version>

Each step runs in its own BEGIN IMMEDIATE transaction and only when schema_migrations records exactly
that step's source version, so a rollback script never runs on a DB that still records a newer version.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
# source version -> (rollback script, resulting version)
STEPS = {12: ("rollback_v12.sql", 11), 11: ("rollback_v11.sql", 10)}
# resulting version -> application artifact to start afterwards
APP_FOR_VERSION = {
    10: "a backend commit whose migrations.LATEST_SCHEMA_VERSION is 10 (e.g. 904dec2 or prod main before O9)",
    11: "a backend commit whose migrations.LATEST_SCHEMA_VERSION is 11 (e.g. fdbb1e5, O9 phases 1-2)",
}
MIN_SQLITE = (3, 35, 0)


def _version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
    return int(row[0] or 0)


def _fail(message: str) -> int:
    print(f"rollback_schema: {message}", file=sys.stderr)
    return 1


def rollback(db: Path, target: int) -> int:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        return _fail("SQLite >= 3.35 is required (ALTER TABLE DROP COLUMN); restore the pre-deploy backup instead")
    if not db.is_file():
        return _fail("database file not found")
    with closing(sqlite3.connect(db, isolation_level=None)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        current = _version(conn)
        if current == target:
            print(f"schema already at version {target}; nothing to do")
            return 0
        # Plan every step first: refuse before any write unless the whole chain exists.
        chain, version = [], current
        while version != target:
            step = STEPS.get(version)
            if step is None or step[1] < target:
                return _fail(f"no rollback path from version {current} to {target}")
            chain.append((version, *step))
            version = step[1]
        for source, script, result in chain:
            sql = (SCRIPTS / script).read_text(encoding="utf-8")
            conn.execute("BEGIN IMMEDIATE")
            try:
                if _version(conn) != source:  # exact source-version guard, re-read under the write lock
                    raise RuntimeError("source version changed")
                for statement in (s.strip() for s in sql.split(";")):
                    lines = [line for line in statement.splitlines() if not line.lstrip().startswith("--")]
                    if "".join(lines).strip():
                        conn.execute("\n".join(lines))
                if _version(conn) != result:
                    raise RuntimeError("unexpected version after rollback step")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                return _fail(f"rollback {source} -> {result} failed; nothing from this step was applied")
            print(f"rolled back schema {source} -> {result}")
    print(f"start: {APP_FOR_VERSION.get(target, f'a backend commit whose LATEST_SCHEMA_VERSION is {target}')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--to", required=True, type=int, choices=sorted({r for _, r in STEPS.values()}))
    args = parser.parse_args(argv)
    return rollback(args.db, args.to)


if __name__ == "__main__":
    sys.exit(main())
