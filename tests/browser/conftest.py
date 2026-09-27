"""Live-server fixtures for the Playwright suite (marked `browser`; skipped without Playwright)."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    db = tmp_path_factory.mktemp("browser") / "demo.db"
    subprocess.run([sys.executable, str(REPO / "scripts" / "seed_tasks_demo.py"), "--db", str(db)], check=True,
                   cwd=REPO, capture_output=True)
    port = _free_port()
    env = {**os.environ, "DB_PATH": str(db), "STORE_RAW_PROMPTS": "0", "TOKEN_INSPECTOR_ALLOWED_HOSTS": "127.0.0.1"}
    for name in ("INGEST_TOKEN", "STORE_TASK_PROMPTS", "JEV_ENABLED", "AI_GATEWAY_API_KEY"):
        env.pop(name, None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/api/meta", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("server did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# Module scope: the sync Playwright API runs an event loop on the main thread while open, which would
# break the pytest-asyncio tests that run after this package.
@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser, server):
    context = browser.new_context()
    page = context.new_page()
    page.base = server
    yield page
    page.unroute_all(behavior="ignoreErrors")
    context.close()


def open_tasks(page):
    page.goto(page.base + "/")
    page.click('[data-view="tasks"]')
    page.wait_for_function("document.querySelectorAll('#table-tasks tbody tr').length > 0 || "
                           "getComputedStyle(document.getElementById('tasks-empty')).display !== 'none'")
