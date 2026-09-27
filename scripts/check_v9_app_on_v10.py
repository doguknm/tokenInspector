"""AC3e: the v9 application (commit 0aab993) starts, ingests and serves analytics on a v10 DB.

Code-only rollback depends on this. Usage (from the repo root, with the project venv):

    python scripts/check_v9_app_on_v10.py

It builds a migrated v10 DB in a temp dir, checks out 0aab993 into a temporary git worktree,
runs that app in a subprocess against the DB and removes the worktree afterwards.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
V9_COMMIT = "0aab993"

CHILD = r"""
import asyncio, httpx
from main import app, lifespan

async def main():
    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/events", headers={"X-Project-Name": "v9check"},
                                  json={"model": "claude-sonnet-4-6", "prompt_tokens": 10, "completion_tokens": 5})
            s = await client.get("/api/analytics/summary")
            print("POST", r.status_code, "SUMMARY", s.status_code)
            assert r.status_code == 201 and s.status_code == 200

asyncio.run(main())
"""


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="v9-on-v10-") as tmp:
        db = Path(tmp) / "v10.db"
        env = {**os.environ, "DB_PATH": str(db), "STORE_RAW_PROMPTS": "0"}
        env.pop("INGEST_TOKEN", None)
        subprocess.run(
            [sys.executable, "-c", "import asyncio, database; asyncio.run(database.init_db())"],
            cwd=REPO, env=env, check=True,
        )
        worktree = Path(tmp) / "v9"
        subprocess.run(["git", "worktree", "add", "--detach", str(worktree), V9_COMMIT], cwd=REPO, check=True,
                       capture_output=True)
        try:
            result = subprocess.run([sys.executable, "-c", CHILD], cwd=worktree, env=env, capture_output=True, text=True)
            print(result.stdout.strip() or result.stderr.strip()[-2000:])
            return result.returncode
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO, check=False)


if __name__ == "__main__":
    sys.exit(main())
