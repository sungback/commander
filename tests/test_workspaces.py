"""Named layout persistence and safe error handling."""

import json
from copy import deepcopy

import pytest

from commander import workspaces


@pytest.fixture
def state(tmp_path):
    tab = {"path": str(tmp_path), "archive": "", "inner": "", "sort_col": 1,
           "sort_order": 0, "show_hidden": False, "current": "", "selected": [], "locked": True}
    return {"panels": [{"active": 0, "tabs": [deepcopy(tab)]},
                       {"active": 0, "tabs": [deepcopy(tab)]}], "active_panel": 1}


def test_round_trip_with_lock_and_unicode_names(tmp_path, state):
    path = tmp_path / "nested" / "workspaces.json"
    workspaces.save(path, {"업무": state})
    assert workspaces.load(path) == {"업무": state}
    assert not list(path.parent.glob(".workspaces-*.tmp"))


def test_corrupted_file_is_not_silently_loaded_or_overwritten(tmp_path):
    path = tmp_path / "workspaces.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(workspaces.InvalidWorkspaceFile):
        workspaces.load(path)
    assert path.read_text(encoding="utf-8") == "not json"


@pytest.mark.parametrize("mutation", [
    lambda state: state.update(active_panel=9),
    lambda state: state["panels"][0].update(active=3),
    lambda state: state["panels"][0]["tabs"][0].update(sort_col=99),
    lambda state: state["panels"][0]["tabs"][0].update(locked="yes"),
    lambda state: state["panels"][0]["tabs"][0].update(selected=[None]),
])
def test_bad_state_is_rejected_on_load_and_save(tmp_path, state, mutation):
    mutation(state)
    assert not workspaces.valid_state(state)
    path = tmp_path / "layouts.json"
    path.write_text(json.dumps({"version": 1, "workspaces": {"x": state}}), encoding="utf-8")
    with pytest.raises(workspaces.InvalidWorkspaceFile):
        workspaces.load(path)
    with pytest.raises(ValueError):
        workspaces.save(tmp_path / "new.json", {"x": state})


def test_failure_to_replace_preserves_previous_file(tmp_path, state, monkeypatch):
    path = tmp_path / "layouts.json"
    workspaces.save(path, {"old": state})

    def fail(*_):
        raise OSError("failed")

    monkeypatch.setattr(workspaces.os, "replace", fail)
    with pytest.raises(OSError):
        workspaces.save(path, {"new": state})
    assert workspaces.load(path) == {"old": state}
    assert not list(tmp_path.glob(".workspaces-*.tmp"))
