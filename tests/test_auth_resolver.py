from __future__ import annotations

import json
import os

import pytest

from mnemos_bridge_core import AuthResolver


def write_config_files(tmp_path, *, config_key: str = "config-key", master_key: str = "master-key") -> None:
    config_dir = tmp_path / ".mnemos"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text(f'api_key = "{config_key}"\n', encoding="utf-8")
    (tmp_path / ".api_keys_master.json").write_text(
        json.dumps({"mnemos": {"bridge": {"api_key": master_key}}}),
        encoding="utf-8",
    )


def test_explicit_arg_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MNEMOS_API_KEY", "env-key")
    write_config_files(tmp_path)

    assert AuthResolver.resolve("explicit-key") == "explicit-key"


def test_env_var_wins_over_files(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MNEMOS_API_KEY", "env-key")
    write_config_files(tmp_path)

    assert AuthResolver.resolve() == "env-key"


def test_config_file_used_before_opted_in_keys_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MNEMOS_API_KEY", raising=False)
    write_config_files(tmp_path, config_key="config-key", master_key="master-key")
    (tmp_path / ".mnemos" / "config.toml").chmod(0o600)
    keys_file = tmp_path / ".api_keys_master.json"
    keys_file.chmod(0o600)
    monkeypatch.setenv("MNEMOS_BRIDGE_KEYS_FILE", str(keys_file))

    assert AuthResolver.resolve() == "config-key"


def test_keys_file_used_when_config_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MNEMOS_API_KEY", raising=False)
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(json.dumps({"mnemos": {"bridge": {"api_key": "master-key"}}}), encoding="utf-8")
    keys_file.chmod(0o600)
    monkeypatch.setenv("MNEMOS_BRIDGE_KEYS_FILE", str(keys_file))

    assert AuthResolver.resolve() == "master-key"


def test_raises_if_nothing_found(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MNEMOS_API_KEY", raising=False)

    with pytest.raises(ValueError, match="MNEMOS API key not found"):
        AuthResolver.resolve()


def test_mcp_token_resolver_uses_distinct_token(monkeypatch) -> None:
    monkeypatch.setenv("MNEMOS_MCP_TOKEN", "mcp-secret")
    monkeypatch.setenv("MNEMOS_API_KEY", "backend-api-key")

    assert AuthResolver.resolve_mcp_token() == "mcp-secret"
    assert AuthResolver.resolve() == "backend-api-key"


def test_mcp_token_resolver_requires_explicit_mapping_token(monkeypatch) -> None:
    monkeypatch.delenv("MNEMOS_MCP_TOKEN", raising=False)
    monkeypatch.setenv("MNEMOS_MCP_TOKENS", "alice:mcp-secret:backend-api-key")

    with pytest.raises(ValueError, match="pass the selected user's MCP token explicitly"):
        AuthResolver.resolve_mcp_token()

    assert AuthResolver.resolve_mcp_token("mcp-secret") == "mcp-secret"


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666])
def test_config_file_rejected_when_group_or_world_accessible(monkeypatch, tmp_path, mode: int) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MNEMOS_API_KEY", raising=False)
    config_dir = tmp_path / ".mnemos"
    config_dir.mkdir()
    config = config_dir / "config.toml"
    config.write_text('api_key = "config-key"\n', encoding="utf-8")
    config.chmod(mode)

    with pytest.raises(ValueError, match="group/world"):
        AuthResolver.resolve()


def test_config_file_accepted_when_private(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MNEMOS_API_KEY", raising=False)
    config_dir = tmp_path / ".mnemos"
    config_dir.mkdir()
    config = config_dir / "config.toml"
    config.write_text('api_key = "config-key"\n', encoding="utf-8")
    config.chmod(0o600)

    assert AuthResolver.resolve() == "config-key"


def test_master_file_not_read_without_opt_in(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MNEMOS_API_KEY", raising=False)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("file permissions are not enforced for root")
    monkeypatch.delenv("MNEMOS_BRIDGE_KEYS_FILE", raising=False)
    (tmp_path / ".api_keys_master.json").write_text(
        json.dumps({"mnemos": {"bridge": {"api_key": "master-key"}}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="MNEMOS API key not found"):
        AuthResolver.resolve()


def test_master_file_read_when_opted_in_via_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MNEMOS_API_KEY", raising=False)
    keys_file = tmp_path / "custom-keys.json"
    keys_file.write_text(json.dumps({"mnemos": {"bridge": {"api_key": "master-key"}}}), encoding="utf-8")
    keys_file.chmod(0o600)
    monkeypatch.setenv("MNEMOS_BRIDGE_KEYS_FILE", str(keys_file))

    assert AuthResolver.resolve() == "master-key"


def test_opted_in_keys_file_rejected_when_group_or_world_accessible(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MNEMOS_API_KEY", raising=False)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("file permissions are not enforced for root")
    keys_file = tmp_path / "custom-keys.json"
    keys_file.write_text(json.dumps({"mnemos": {"bridge": {"api_key": "master-key"}}}), encoding="utf-8")
    keys_file.chmod(0o644)
    monkeypatch.setenv("MNEMOS_BRIDGE_KEYS_FILE", str(keys_file))

    with pytest.raises(ValueError, match="group/world"):
        AuthResolver.resolve()
