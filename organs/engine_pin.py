#!/usr/bin/env python3
"""engine_pin -- which brain an engine call answers for, and where its output goes, held per call.

The engines answer for a module-global `DB_PATH` and print one JSON line. A caller that pointed the global at another
file for the length of a call, and swapped `sys.stdout` to catch the line, was correct in a program with one thread and
wrong in every other: two calls in flight swapped each other's target, and one brain's question was answered from the
other.

TWO THINGS, AND WHICH ONE IS THE GUARANTEE. `pinned()` holds one re-entrant lock for the whole call: one engine call runs
at a time in this process. That is the guarantee, because the model code under an engine call is not safe to run twice at
once either. The pin itself is a context variable read by `db_path()`, so the answer to "which file" travels with the
call and never through a global. Neither reaches another process: two processes are kept apart by SQLite.

A thread the engine starts inside a pinned call does not inherit the pin. `db_path()` refuses there rather than answer
for the default brain.

Stdlib only.
"""
from __future__ import annotations

import contextlib
import contextvars
import sys
import threading
from pathlib import Path
from typing import Any, Iterator, NamedTuple, Optional, Union

LOCK = threading.RLock()


class Pin(NamedTuple):
    db: Path
    sink: Any


class PinUnset(RuntimeError):
    """A thread asked which brain to use while another thread's pinned call is running and it carries no pin."""

    code = "ENGINE_PIN_UNSET"
    detail: dict = {}


_PIN: "contextvars.ContextVar[Optional[Pin]]" = contextvars.ContextVar("voidloom_engine_pin", default=None)
_owner: Optional[int] = None


def current() -> Optional[Pin]:
    return _PIN.get()


def db_path(default: Union[str, Path]) -> Path:
    """The brain of the pinned call this code runs inside, else `default`."""
    pin = _PIN.get()
    if pin is not None:
        return pin.db
    owner = _owner
    if owner is not None and owner != threading.get_ident():
        raise PinUnset("ENGINE_PIN_UNSET: this thread carries no database pin while another thread's engine call is "
                       "running; run the call through voidloom._facade, or copy the context into the thread")
    return Path(default)


class _PinnedStdout:
    """`sys.stdout` while a call is pinned: a write from the pinned context goes to its sink, any other write goes where
    it always went."""

    def __init__(self, under: Any):
        self._under = under

    def _target(self) -> Any:
        pin = _PIN.get()
        return pin.sink if pin is not None and pin.sink is not None else self._under

    def write(self, text: str) -> int:
        return self._target().write(text)

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        self._target().flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._under, name)


@contextlib.contextmanager
def pinned(db: Union[str, Path], sink: Any = None) -> Iterator[None]:
    """One engine call over `db`, its stdout into `sink` (None: stdout is left alone). Re-entrant in one thread."""
    global _owner
    with LOCK:
        token = _PIN.set(Pin(Path(db), sink))
        before, _owner = _owner, threading.get_ident()
        stdout = sys.stdout
        if sink is not None:
            sys.stdout = _PinnedStdout(stdout)
        try:
            yield
        finally:
            sys.stdout = stdout
            _owner = before
            _PIN.reset(token)
