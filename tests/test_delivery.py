from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from sigvue.profile import load_browser_profile

from nexrad_viewer import cli
from nexrad_viewer.runtime import runtime_profile


def test_runtime_profile_uses_explicit_durable_paths():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        data = root / "radar data"
        output = root / "rendered output"
        data.mkdir()

        with runtime_profile(data_root=data, output_root=output) as profile_path:
            profile = load_browser_profile(profile_path)
            payload = tomllib.loads(profile_path.read_text(encoding="utf-8"))

        assert profile.title == "NOAA NEXRAD Viewer"
        assert len(profile.workspaces) == 2
        workspace = profile.workspaces[0]
        assert workspace.module_name == "nexrad_viewer.workspace"
        assert workspace.attribute == "create_workspace"
        assert workspace.flatten_discovery is False
        assert Path(workspace.configuration["data_root"]) == data.resolve()
        assert Path(workspace.configuration["output_root"]) == output.resolve()
        national = profile.workspaces[1]
        assert national.module_name == "nexrad_viewer.national.workspace"
        assert national.attribute == "create_workspace"
        assert national.flatten_discovery is True
        assert payload["workspaces"][0]["id"] == "nexrad-sites"
        assert payload["workspaces"][1]["id"] == "nexrad-national"
        assert output.is_dir()
        assert not profile_path.exists()


def test_nexrad_cli_supplies_profile_and_preserves_sigvue_arguments():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        observed = {}

        def inspect_invocation():
            arguments = list(sys.argv[1:])
            profile = Path(arguments[arguments.index("--config") + 1])
            observed["arguments"] = arguments
            observed["profile"] = load_browser_profile(profile)

        with (
            patch.object(
                sys,
                "argv",
                [
                    "nexrad-viewer",
                    "batch",
                    "--data-root",
                    str(root / "data"),
                    "--output-root",
                    str(root / "outputs"),
                    "--list",
                ],
            ),
            patch.object(cli, "sigvue_main", side_effect=inspect_invocation),
        ):
            cli.main()

        assert observed["arguments"][0:2] == [
            "--config",
            observed["arguments"][1],
        ]
        assert observed["arguments"][2:] == ["batch", "--list"]
        workspace = observed["profile"].workspaces[0]
        assert Path(workspace.configuration["data_root"]) == (root / "data").resolve()
        assert (
            Path(workspace.configuration["output_root"]) == (root / "outputs").resolve()
        )


def test_package_leaves_delivery_to_sigvue():
    project = Path(__file__).resolve().parents[1]
    payload = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = payload["project"]["scripts"]

    assert scripts["nexrad-viewer"] == "nexrad_viewer.cli:main"
    assert "nexrad-viewer-desktop" not in scripts
    assert "nexrad-viewer-build" not in scripts
    assert "nexrad-download" not in scripts
    assert not (project / "src" / "nexrad_viewer" / "download.py").exists()
    assert {
        requirement.split(">=", 1)[0]
        for requirement in payload["project"]["dependencies"]
    } == {"certifi", "kaleido", "numpy", "pillow", "plotly", "sigvue"}
    assert "desktop" not in payload["project"]["optional-dependencies"]
    assert not (project / "src" / "nexrad_viewer" / "desktop.py").exists()
    assert not (
        project / "src" / "nexrad_viewer" / "_packaging" / "build.py"
    ).exists()
    assert not (
        project / "src" / "nexrad_viewer" / "_packaging" / "nexrad_viewer.spec"
    ).exists()
