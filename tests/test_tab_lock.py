"""Locked tabs resist ordinary closure and survive session restoration."""
import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from commander.panel import FilePanel


@pytest.fixture
def panel(tmp_path):
    app = QApplication.instance() or QApplication([])
    panel = FilePanel(str(tmp_path))
    yield panel
    for i in range(panel.tabs.count()):
        tab = panel.tabs.widget(i)
        worker = tab._size_worker
        tab._stop_size_worker()
        if worker is not None:
            worker.wait()
    panel.close()
    panel.deleteLater()
    QCoreApplication.sendPostedEvents(panel, QEvent.DeferredDelete)
    app.processEvents()


def test_locked_tab_resists_close_and_title_follows_navigation(panel, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    panel.new_tab(str(other))
    panel.toggle_tab_lock()
    assert panel.tabs.widget(1).locked
    assert panel.tabs.tabText(1).startswith("🔒 ")
    panel.close_tab()
    panel.tabs.tabCloseRequested.emit(1)
    assert panel.tabs.count() == 2
    panel.set_path(str(tmp_path))
    assert panel.tabs.tabText(1) == f"🔒 {tmp_path.name}"
    panel.toggle_tab_lock()
    panel.close_tab()
    assert panel.tabs.count() == 1


def test_tab_lock_state_restored_and_explicit_replace_overrides_lock(panel, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    panel.new_tab(str(other))
    panel.set_tab_locked(0, True)
    panel.set_tab_locked(1, True)
    state = panel.to_state()
    assert [tab["locked"] for tab in state["tabs"]] == [True, True]
    panel.replace_state(state)
    assert panel.tabs.count() == 2
    assert [panel.tabs.widget(i).locked for i in range(2)] == [True, True]
    assert panel.tabs.currentIndex() == 1
    assert all(panel.tabs.tabText(i).startswith("🔒 ") for i in range(2))
    # Explicit workspace replacement can remove locked tabs; legacy state is unlocked.
    panel.replace_state({"active": 0, "tabs": [{"path": str(tmp_path)}]})
    assert panel.tabs.count() == 1
    assert panel.current_path() == str(tmp_path)
    assert not panel.tabs.widget(0).locked
