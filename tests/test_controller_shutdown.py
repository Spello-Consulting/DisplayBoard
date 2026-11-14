"""Tests that AppController.shutdown() only runs once, however it is reached.

On SIGTERM, shutdown() can be called at the same moment from the controller thread (when
run() exits its loop) and from the main thread (via ThreadManager.stop_all()). See issue #30.
"""
from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

import controller as controller_module
from controller import AppController
from thread_manager import RestartPolicy, ThreadManager

SHUTDOWN_COMPLETE_MSG = "Controller shutdown complete."


def _config_get(_section: str, _key: str, default: Any = None) -> Any:
    """Stand-in for SCConfigManager.get() that always returns the default.

    Returns:
        The default value passed in.
    """
    return default


@pytest.fixture
def logger() -> MagicMock:
    """A mock SCLogger whose log_message() is slow enough for two callers to overlap.

    Returns:
        The mock logger.
    """
    mock_logger = MagicMock()
    mock_logger.get_fatal_error.return_value = False
    mock_logger.log_message.side_effect = lambda *_args, **_kwargs: time.sleep(0.05)
    return mock_logger


@pytest.fixture
def app_controller(monkeypatch: pytest.MonkeyPatch, logger: MagicMock) -> AppController:
    """An AppController with mocked config, logger and data manager.

    Returns:
        The controller under test.
    """
    monkeypatch.setattr(controller_module, "DataManager", MagicMock())
    config = MagicMock()
    config.get.side_effect = _config_get
    config.check_for_config_changes.return_value = None
    return AppController(config, logger, threading.Event())


def _shutdown_complete_count(logger: MagicMock) -> int:
    """Count how many times the shutdown-complete message was logged.

    Returns:
        The number of matching log_message() calls.
    """
    return sum(1 for c in logger.log_message.call_args_list if c.args and c.args[0] == SHUTDOWN_COMPLETE_MSG)


def test_concurrent_shutdown_runs_once(app_controller: AppController, logger: MagicMock) -> None:
    """Two threads calling shutdown() at the same time do the shutdown work exactly once."""
    barrier = threading.Barrier(2)

    def call_shutdown() -> None:
        barrier.wait()
        app_controller.shutdown()

    threads = [threading.Thread(target=call_shutdown) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert _shutdown_complete_count(logger) == 1


def test_repeated_shutdown_runs_once(app_controller: AppController, logger: MagicMock) -> None:
    """Calling shutdown() again after it has finished does nothing."""
    app_controller.shutdown()
    app_controller.shutdown()

    assert _shutdown_complete_count(logger) == 1


def test_stop_wakes_loop_without_shutting_down(app_controller: AppController, logger: MagicMock) -> None:
    """stop() only wakes the main loop; it must not run the shutdown work."""
    app_controller.wake_event.clear()

    app_controller.stop()

    assert app_controller.wake_event.is_set()
    assert _shutdown_complete_count(logger) == 0


def test_thread_manager_stop_all_shuts_down_once(app_controller: AppController, logger: MagicMock) -> None:
    """The SIGTERM path (stop event set, then stop_all()) shuts the controller down exactly once."""
    stop_event = threading.Event()
    tm = ThreadManager(logger, global_stop=stop_event, exit_on_fatal=False)
    tm.add(
        name="controller",
        target=app_controller.run,
        kwargs={"stop_event": stop_event},
        restart=RestartPolicy(mode="never"),
    )
    tm.start_all()
    time.sleep(0.2)  # Let the controller enter its wait

    # What the SIGTERM handler and main.py's finally block do
    stop_event.set()
    app_controller.wake_event.set()
    tm.stop_all()
    tm.join_all(timeout_per_thread=5.0)

    assert _shutdown_complete_count(logger) == 1
