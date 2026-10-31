"""Tests that board pages load current static assets and survive an out of date board.js."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

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
