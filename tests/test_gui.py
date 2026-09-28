"""UI lifecycle checks use a fake runtime and never initialize a physical device."""
import threading
import time
import tkinter as tk

import pytest

from pianotuner.ui.gui import TunerWindow, parse_target


def test_gui_target_validation():
    assert parse_target("A4") == 440
    assert parse_target("220") == 220
    for value in ("nan", "inf", "219", "441", "C3", "bad target"):
        with pytest.raises(ValueError):
            parse_target(value)


def test_gui_runtime_worker_stop_and_input_lock(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display unavailable on this host")
    root.withdraw()
    entered = threading.Event()
    finished = threading.Event()
    owner_thread = threading.get_ident()
    worker_thread = []

    class Session:
        def __init__(self, **kwargs):
            worker_thread.append(threading.get_ident())

        def run(self, callback, stop_event, **kwargs):
            callback({"mode": "SIMULATION", "state": "WAIT_STRIKE", "prompt": "Strike the selected string"})
            entered.set()
            if not stop_event.wait(5):
                raise RuntimeError("GUI failed to stop the worker")
            finished.set()
            return {"outcome": "ABORTED"}

    view = TunerWindow(root, Session, tmp_path)
    try:
        view.start()
        assert entered.wait(2)
        assert worker_thread == [view.worker.ident]
        assert worker_thread[0] != owner_thread
        assert str(view.target_control.cget("state")) == "disabled"
        assert str(view.mode_control.cget("state")) == "disabled"
        assert str(view.stop_button.cget("state")) == "normal"
        view.close()
        assert finished.wait(2)
        view.worker.join(2)
        assert not view.worker.is_alive()
        assert view.stop_event.is_set()
        # Let the main-thread polling callback finish the window shutdown.
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                root.update()
                if not root.winfo_exists():
                    break
            except tk.TclError:
                break
            time.sleep(.01)
    finally:
        view.stop_event.set()
        if view.worker:
            view.worker.join(2)
        try:
            root.destroy()
        except tk.TclError:
            pass
