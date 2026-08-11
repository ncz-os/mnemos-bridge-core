from __future__ import annotations

from pathlib import Path

import tomllib


def test_mcp_dependency_is_bounded_to_supported_sdk_majors() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))

    assert "mcp>=1.0,<3" in project["project"]["dependencies"]
