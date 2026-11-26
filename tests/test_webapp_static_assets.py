"""Tests that board pages load current static assets and survive an out of date board.js."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import webapp
from webapp import _get_static_version, create_asgi_app  # noqa: PLC2701

if TYPE_CHECKING:
    from pathlib import Path

TEMPLATES = ["primary.html", "calendar.html", "energy_detail.html"]


def _get_page(template: str) -> str:
    values: dict[tuple[str, str], Any] = {("DisplayBoards", "Boards"): [{"Name": "Test", "Template": template}]}
    config = MagicMock()
    config.get.side_effect = lambda section, key=None, default=None: values.get((section, key), default)
    controller = MagicMock()
    controller.get_webapp_data.return_value = {"global": {}}
    app, _ = create_asgi_app(controller, config, MagicMock())
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    return response.text


def test_static_version_changes_with_content(tmp_path: Path) -> None:
    """The version is stable for the same files and changes when either file changes."""
    (tmp_path / "board.js").write_text("one")
    (tmp_path / "styles.css").write_text("two")
    version = _get_static_version(tmp_path)
    assert _get_static_version(tmp_path) == version

    (tmp_path / "board.js").write_text("changed")
    js_version = _get_static_version(tmp_path)
    assert js_version != version

    (tmp_path / "styles.css").write_text("changed")
    assert _get_static_version(tmp_path) != js_version


@pytest.mark.parametrize("template", TEMPLATES)
def test_page_links_versioned_assets(template: str) -> None:
    """Every board links its script and stylesheet with a version."""
    page = _get_page(template)
    assert 'href="/static/styles.css?v=' in page
    assert 'src="/static/board.js?v=' in page
    assert '?v="' not in page


@pytest.mark.parametrize("template", TEMPLATES)
def test_browser_refresh_is_scheduled_after_topic_handlers(template: str) -> None:
    """An error scheduling the refresh can't stop the topic handlers from being registered."""
    page = _get_page(template)
    assert page.rfind("BoardClient.onTopic(") < page.index("scheduleBrowserRefresh(")


def test_serve_configures_bounded_graceful_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """serve_asgi_blocking builds uvicorn with a bounded graceful-shutdown timeout.

    The web server runs in a non-daemon thread. If uvicorn is left with an unbounded
    graceful-shutdown timeout, a lingering WebSocket connection (e.g. an open dashboard)
    keeps that thread alive indefinitely and blocks the whole process from exiting until
    it is SIGKILLed. ``serve_asgi_blocking`` must give uvicorn a bounded timeout.
    """
    captured: dict[str, Any] = {}

    class _FakeServer:
        def __init__(self, config: Any) -> None:
            captured["config"] = config
            self.should_exit = False

        def install_signal_handlers(self) -> None:  # pragma: no cover - replaced at runtime
            pass

        async def serve(self) -> None:  # noqa: PLR6301 - mirrors uvicorn.Server.serve
            return None

    monkeypatch.setattr(webapp.uvicorn, "Server", _FakeServer)

    values: dict[tuple[str, str], Any] = {}
    config = MagicMock()
    config.get.side_effect = lambda section, key=None, default=None: values.get((section, key), default)

    stop_event = threading.Event()
    stop_event.set()  # resolve the stop watcher immediately

    webapp.serve_asgi_blocking(MagicMock(), config, MagicMock(), stop_event)

    uv_config = captured["config"]
    assert uv_config.timeout_graceful_shutdown is not None, "uvicorn must have a bounded graceful-shutdown timeout"
    assert uv_config.timeout_graceful_shutdown == int(webapp.WEB_SERVER_GRACEFUL_SHUTDOWN_TIMEOUT)
    assert 0 < uv_config.timeout_graceful_shutdown <= 30
