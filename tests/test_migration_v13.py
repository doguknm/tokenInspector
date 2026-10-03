from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

import database
import migrations
from scripts import rollback_schema


pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 35, 0), reason="DROP COLUMN needs SQLite 3.35+")


def schema(path):
    with closing(sqlite3.connect(path)) as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(token_events)")]
        indexes = [row[1] for row in conn.execute("PRAGMA index_list(token_events)")]
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        revision = conn.execute("SELECT revision FROM export_state WHERE id = 1").fetchone()[0]
    return columns, indexes, version, revision


async def test_migration_v13_and_rollback_round_trip(tmp_path):
    path = tmp_path / "v13.db"
    await database.init_db(str(path))
    columns, indexes, version, revision = schema(path)
    assert version == migrations.LATEST_SCHEMA_VERSION == 13
    assert "agent" in columns and "ix_token_events_call_time_agent" in indexes
    assert revision == 1

    assert rollback_schema.main(["--db", str(path), "--to", "12"]) == 0
    columns, indexes, version, revision = schema(path)
    assert version == 12 and "agent" not in columns and "ix_token_events_call_time_agent" not in indexes
    assert revision == 2

    await database.init_db(str(path))
    columns, indexes, version, revision = schema(path)
    assert version == 13 and "agent" in columns and "ix_token_events_call_time_agent" in indexes
    assert revision == 3
