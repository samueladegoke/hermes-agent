from pathlib import Path

from gateway.meta_router_paths import meta_router_dir, openclaw_workspace, rql_dir, rql_scripts_dir


def test_openclaw_workspace_prefers_explicit_env(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path / "ignored"))

    assert openclaw_workspace() == tmp_path
    assert meta_router_dir() == tmp_path / "skills" / "maintainer" / "meta-router"
    assert rql_dir() == tmp_path / "rql"
    assert rql_scripts_dir() == tmp_path / "rql" / "scripts"


def test_openclaw_workspace_defaults_to_hermes_home(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes-home"
    expected = hermes_home / "workspace" / "openclaw-workspace"
    monkeypatch.delenv("HERMES_OPENCLAW_WORKSPACE", raising=False)
    monkeypatch.delenv("OPENCLAW_WORKSPACE", raising=False)
    monkeypatch.delenv("MR_ALS_WORKSPACE", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    assert openclaw_workspace() == expected


def test_component_paths_can_be_overridden_independently(monkeypatch, tmp_path):
    mr_dir = tmp_path / "mr"
    rql_root = tmp_path / "rql-root"
    monkeypatch.setenv("HERMES_META_ROUTER_PATH", str(mr_dir))
    monkeypatch.setenv("HERMES_RQL_DIR", str(rql_root))

    assert meta_router_dir() == mr_dir
    assert rql_dir() == rql_root
    assert rql_scripts_dir() == rql_root / "scripts"
