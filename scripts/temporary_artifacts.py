"""Scratch output for verification; never retained in the project."""

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory


@contextmanager
def temporary_artifacts(requested=None, *, filename=False):
    """Legacy output arguments are labels, not persistent destinations.

    Consume or inspect files inside the context. Its fresh system temporary
    directory is removed on normal exit and on Python exceptions.
    """
    if requested is None:
        yield None
        return
    with TemporaryDirectory(prefix="careerpilot-verification-") as directory:
        target = Path(directory)
        if filename:
            target /= Path(requested).name
        yield target
