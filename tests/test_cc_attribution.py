"""Claude Code project attribution (O9 C4; AC2.2, AC2.3)."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from cc_support import make_repo, modules, set_home

FIXTURE = Path(__file__).parent / "fixtures" / "attribution_vectors.json"
PLUGIN_FIXTURE = Path(__file__).resolve().parents[2] / "token_inspector" / "tests" / "fixtures" / "attribution_vectors.json"


@pytest.fixture
def cc(tmp_path, monkeypatch):
    set_home(monkeypatch, tmp_path / "home")
    return modules()


def test_attribution_order(cc, tmp_path, caplog):
    at = cc["cc_attribution"]
    repo = make_repo(tmp_path / "FolderName", "git@github.com:Org/SlugName.git")
    work = repo / "sub" / "dir"
    work.mkdir(parents=True)
    # alias (longest matching root) wins over the origin slug
    aliases = {str(repo): "alias-outer", str(repo / "sub"): "alias-inner"}
    assert at.resolve(str(work), aliases) == ("alias-inner", "alias")
    # origin slug over git root
    assert at.resolve(str(work), {}) == ("slugname", "git_remote")
    # git root name when there is no origin
    bare = make_repo(tmp_path / "Plain Repo")
    assert at.resolve(str(bare), {}) == ("plain-repo", "git_root")
    # no git -> fallback
    lone = tmp_path / "lonely"
    lone.mkdir()
    assert at.resolve(str(lone), {}) == ("claude-code", "fallback")
    assert at.resolve(None, {}) == ("claude-code", "fallback")
    # invalid alias names are dropped by the config loader (strict rule, never normalized)
    assert cc["cc_config"]._aliases({str(repo): "Bad Name!", str(tmp_path): "ok-name"}) == {str(tmp_path): "ok-name"}
    assert not caplog.records


def test_git_file_with_gitdir_and_worktree_commondir(cc, tmp_path):
    at = cc["cc_attribution"]
    main = make_repo(tmp_path / "main", "https://example.org/Org/Shared.git")
    gitdir = main / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    wt = tmp_path / "wt-checkout"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    assert at.resolve(str(wt), {}) == ("shared", "git_remote")
    rel = tmp_path / "rel"
    rel.mkdir()
    (rel / "realgit").mkdir()
    (rel / "realgit" / "config").write_text('[remote "origin"]\n\turl = git@h:X/Rel.git\n', encoding="utf-8")
    (rel / ".git").write_text("gitdir: realgit\n", encoding="utf-8")
    assert at.resolve(str(rel), {}) == ("rel", "git_remote")


def test_only_a_name_leaves_the_resolver(cc, tmp_path):
    at = cc["cc_attribution"]
    repo = make_repo(tmp_path / "Secret-Folder", "https://zz-user:sk-ant-CANARYSECRET0123@example.org/Org/Named.git")
    result = at.resolve(str(repo), {})
    assert result == ("named", "git_remote")
    assert all(isinstance(v, str) and "/" not in v and "\\" not in v and "CANARY" not in v for v in result)


def test_hostname_and_ipv4_names_fall_through(cc, tmp_path, monkeypatch):
    at = cc["cc_attribution"]
    monkeypatch.setattr(socket, "gethostname", lambda: "Zz-Canary-Box.internal.lan")
    monkeypatch.setattr(socket, "getfqdn", lambda: "zz-canary-box.internal.lan")
    repo = make_repo(tmp_path / "10.1.2.3", "git@h:Org/zz-canary-box.git")
    assert at.resolve(str(repo), {str(repo): "zz-canary-box.internal.lan"}) == ("claude-code", "fallback")
    ok = make_repo(tmp_path / "zz-canary-box", "git@h:Org/Real.git")
    assert at.resolve(str(ok), {str(ok): "zz-canary-box"}) == ("real", "git_remote")


def test_normalized_hostname_git_root_falls_through(cc, tmp_path, monkeypatch):
    at = cc["cc_attribution"]
    monkeypatch.setattr(socket, "gethostname", lambda: "Build+Node")
    monkeypatch.setattr(socket, "getfqdn", lambda: "Build+Node")
    repo = make_repo(tmp_path / "build-node")
    assert at.resolve(str(repo), {}) == ("claude-code", "fallback")


def test_devir_clone_resolves_to_origin_slug(cc, tmp_path):
    at = cc["cc_attribution"]
    clone = make_repo(tmp_path / "PEGADocRagAgent-devir", "git@github.com:someone/PEGADocRag.git")
    assert at.resolve(str(clone), {}) == ("pegadocrag", "git_remote")


def test_attribution_vectors(cc):
    at = cc["cc_attribution"]
    vectors = json.loads(FIXTURE.read_bytes())
    for item in vectors["remote_urls"]:
        assert at.slug_from_url(item["url"]) == item["expected"], item["expected"]
    for item in vectors["directory_names"]:
        assert at.normalize_name(item["name"]) == item["expected"], item["expected"]


def test_attribution_vectors_identical():
    if not PLUGIN_FIXTURE.exists():
        pytest.skip("plugin repo not checked out next to this repo (must run at the integration review)")
    assert FIXTURE.read_bytes() == PLUGIN_FIXTURE.read_bytes()
