"""Heavy geometry work in separate processes.

Open CASCADE calls hold Python's GIL, so running them on a thread still freezes the window. Each worker
process loads the model (STEP, or a Gerber board outline) once and then projects views on request; the GUI only receives the 2D results.
"""
from __future__ import annotations

import multiprocessing as mp
import os
from concurrent.futures import Future, ProcessPoolExecutor

_SHAPE = None


def _init(path: str, y_up: bool):
    global _SHAPE
    from .geometry import load_model

    _SHAPE = load_model(path, y_up=y_up)


def _info():
    from .geometry import shape_info

    return shape_info(_SHAPE)


def _project(src: str, hidden: bool, fast: bool):
    from .geometry import project, project_fast

    return (project_fast if fast else project)(_SHAPE, src, with_hidden=hidden)


class Engine:
    """A pool of worker processes bound to one STEP file."""

    def __init__(self, path: str, y_up: bool, workers: int | None = None):
        n = workers or max(1, min(4, (os.cpu_count() or 2) - 1))
        self.pool = ProcessPoolExecutor(max_workers=n, mp_context=mp.get_context("spawn"),
                                        initializer=_init, initargs=(str(path), y_up))

    def info(self) -> Future:
        return self.pool.submit(_info)

    def project(self, src: str, hidden: bool, fast: bool = False) -> Future:
        return self.pool.submit(_project, src, hidden, fast)

    def stop(self):
        """Abandon all work immediately (kills the worker processes)."""
        procs = list(getattr(self.pool, "_processes", {}).values())
        self.pool.shutdown(wait=False, cancel_futures=True)
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
