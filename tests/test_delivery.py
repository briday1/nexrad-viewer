from __future__ import annotations

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.request import urlopen

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from sigvue.profile import load_browser_profile

from nexrad_viewer import cli, desktop
from nexrad_viewer.runtime import runtime_profile


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWindow:
    def __init__(self):
        self.events = SimpleNamespace(
            loaded=FakeEvent(),
            restored=FakeEvent(),
        )
        self.scripts = []
        self.fullscreen_toggles = 0
        self.selected_directory = "/tmp/radar-data"

    def evaluate_js(self, script):
        self.scripts.append(script)

    def toggle_fullscreen(self):
        self.fullscreen_toggles += 1

    def create_file_dialog(self, dialog_type):
        self.dialog_type = dialog_type
        return (self.selected_directory,)


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
        assert len(profile.workspaces) == 1
        workspace = profile.workspaces[0]
        assert workspace.module_name == "nexrad_viewer.workspace"
        assert workspace.attribute == "create_workspace"
        assert workspace.flatten_discovery is True
        assert Path(workspace.configuration["data_root"]) == data.resolve()
        assert Path(workspace.configuration["output_root"]) == output.resolve()
        assert payload["workspaces"][0]["id"] == "nexrad"
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


def test_desktop_window_hosts_a_live_private_sigvue_server():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        result = {}
        window = FakeWindow()

        def create_window(title, url, **options):
            result.update(title=title, url=url, options=options)
            return window

        def start(**options):
            with urlopen(f"{result['url']}/health", timeout=5) as response:
                result["health"] = json.load(response)
            for handler in window.events.loaded.handlers:
                handler(window)
            result["start_options"] = options

        fake_webview = SimpleNamespace(
            FileDialog=SimpleNamespace(FOLDER=20),
            create_window=create_window,
            start=start,
        )
        with (
            patch.dict(sys.modules, {"webview": fake_webview}),
            patch.object(
                sys,
                "argv",
                [
                    "nexrad-viewer-desktop",
                    "--data-root",
                    str(root / "data"),
                    "--output-root",
                    str(root / "outputs"),
                    "--width",
                    "1200",
                    "--height",
                    "700",
                ],
            ),
        ):
            desktop.main()

        assert result["title"] == "NEXRAD Viewer"
        assert result["url"].startswith("http://127.0.0.1:")
        assert result["health"] == {"status": "ok"}
        assert result["options"]["width"] == 1200
        assert result["options"]["height"] == 700
        assert result["options"]["min_size"] == (900, 600)
        bridge = result["options"]["js_api"]
        assert bridge.choose_directory() == "/tmp/radar-data"
        assert window.dialog_type == 20
        assert bridge.toggle_fullscreen() is True
        for handler in window.events.restored.handlers:
            handler()
        assert bridge.fullscreen_state() is False
        assert window.fullscreen_toggles == 1
        assert len(window.scripts) == 2
        assert "#fullscreen-toggle" in window.scripts[0]
        assert "stopImmediatePropagation" in window.scripts[0]
        assert "event.key !== 'Escape'" in window.scripts[0]
        assert window.scripts[1] == ("window.__nexradSetNativeFullscreen?.(false)")
        assert result["start_options"] == {"debug": False}


def test_package_declares_cli_desktop_and_build_entry_points():
    project = Path(__file__).resolve().parents[1]
    payload = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = payload["project"]["scripts"]

    assert scripts["nexrad-viewer"] == "nexrad_viewer.cli:main"
    assert scripts["nexrad-viewer-desktop"] == "nexrad_viewer.desktop:main"
    assert scripts["nexrad-viewer-build"] == "nexrad_viewer._packaging.build:main"
    assert "nexrad-download" not in scripts
    assert not (project / "src" / "nexrad_viewer" / "download.py").exists()
    assert {
        requirement.split(">=", 1)[0]
        for requirement in payload["project"]["dependencies"]
    } == {"numpy", "pillow", "plotly", "sigvue"}
    assert {"pyinstaller", "pywebview"} == {
        requirement.split(">=", 1)[0]
        for requirement in payload["project"]["optional-dependencies"]["desktop"]
    }
    spec = project / "src" / "nexrad_viewer" / "_packaging" / "nexrad_viewer.spec"
    assert spec.is_file()
