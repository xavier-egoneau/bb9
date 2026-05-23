"""Runtime logging helpers."""

from __future__ import annotations

import logging


DEFAULT_FORMAT = "%(levelname)s %(name)s: %(message)s"


def configure_logging(level: str = "WARNING") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.WARNING),
        format=DEFAULT_FORMAT,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"bb9.{name}")
