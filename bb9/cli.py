"""Compatibility entry point for `python -m bb9.cli`."""

from __future__ import annotations

from .core.cli import run_interactive


if __name__ == "__main__":
    raise SystemExit(run_interactive())
