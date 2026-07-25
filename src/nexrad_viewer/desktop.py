"""Native pywebview launcher for the focused NEXRAD application."""

from __future__ import annotations

import argparse
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any

from sigvue.web.application import _make_handler, create_app

from nexrad_viewer.runtime import APPLICATION_NAME, runtime_profile


_NATIVE_FULLSCREEN_SCRIPT = r"""
(() => {
  const button = document.querySelector('#fullscreen-toggle');
  if (!button || button.dataset.nativeFullscreen === 'true') return;
  button.dataset.nativeFullscreen = 'true';

  let active = false;
  const render = value => {
    active = Boolean(value);
    button.setAttribute(
      'aria-label',
      active ? 'Exit fullscreen' : 'Enter fullscreen',
    );
    button.setAttribute('aria-pressed', String(active));
    button.textContent = active ? '×' : '⛶';
    window.dispatchEvent(new Event('resize'));
  };
  window.__nexradSetNativeFullscreen = render;
  const toggle = async () => {
    if (!window.pywebview?.api?.toggle_fullscreen) return;
    button.disabled = true;
    try {
      render(await window.pywebview.api.toggle_fullscreen());
    } finally {
      button.disabled = false;
    }
  };

  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    void toggle();
  }, true);
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape' || !active) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    void toggle();
  }, true);
  if (window.pywebview?.api?.fullscreen_state) {
    void window.pywebview.api.fullscreen_state().then(render);
  }
})();
"""


class _DesktopApi:
    """Small JS bridge for native-only window behavior."""

    def __init__(self) -> None:
        self._window: Any | None = None
        self._fullscreen = False
        self._lock = Lock()

    def _bind(self, window: Any) -> None:
        self._window = window

    def toggle_fullscreen(self) -> bool:
        with self._lock:
            if self._window is None:
                return False
            self._window.toggle_fullscreen()
            self._fullscreen = not self._fullscreen
            return self._fullscreen

    def fullscreen_state(self) -> bool:
        with self._lock:
            return self._fullscreen

    def _restored(self) -> None:
        with self._lock:
            if not self._fullscreen:
                return
            self._fullscreen = False
            window = self._window
        if window is not None:
            window.evaluate_js(
                "window.__nexradSetNativeFullscreen?.(false)"
            )


def _install_native_fullscreen(window: Any) -> None:
    window.evaluate_js(_NATIVE_FULLSCREEN_SCRIPT)


def main() -> None:
    """Start Sigvue privately and host it in a native desktop window."""
    parser = argparse.ArgumentParser(
        description="Open the NOAA NEXRAD Viewer in a native desktop window",
    )
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    try:
        import webview
    except ImportError as exc:  # pragma: no cover - optional desktop extra
        raise SystemExit(
            'Install desktop support first: pip install "nexrad-viewer[desktop]"'
        ) from exc

    with runtime_profile(
        data_root=args.data_root,
        output_root=args.output_root,
        desktop=True,
    ) as profile:
        app = create_app(
            title=APPLICATION_NAME,
            reload_workspaces=False,
            config_path=profile,
        )
        server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(app))
        server.daemon_threads = True
        thread = Thread(
            target=server.serve_forever,
            name="nexrad-viewer-server",
            daemon=True,
        )
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        desktop_api = _DesktopApi()
        window = webview.create_window(
            APPLICATION_NAME,
            url,
            width=max(args.width, 900),
            height=max(args.height, 600),
            min_size=(900, 600),
            js_api=desktop_api,
        )
        desktop_api._bind(window)
        window.events.loaded += _install_native_fullscreen
        window.events.restored += desktop_api._restored
        try:
            webview.start(debug=args.debug)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    main()
