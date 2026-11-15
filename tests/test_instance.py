import pytest

from connor import build_id, system

pytest.importorskip("PySide6")

from connor.gui.app import instance_action  # noqa: E402


def test_instance_messages():
    assert instance_action("show abc123\n", "abc123") == "show"
    assert instance_action("show", "abc123") == "show", "old Connor versions send only «show»"
    assert instance_action("show def456\n", "abc123") == "replace"
    assert instance_action("quit\n", "abc123") == "quit"
    assert instance_action("", "abc123") == "ignore"


def test_build_id_is_stable():
    assert build_id() == build_id()
    assert len(build_id()) == 12


def test_never_touches_processes_outside_connors_venv():
    assert system.own_venv() is None
    assert system.connor_processes() == []
    assert system.stop_running_connor() is False
