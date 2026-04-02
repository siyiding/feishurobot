"""Logging utilities."""
import logging
import sys
from typing import Optional

from app.core.config import get_settings


def setup_logging(name: Optional[str] = None, level: Optional[str] = None) -> logging.Logger:
    """Set up a logger with consistent formatting."""
    settings = get_settings()
    log_level = level or settings.log_level

    logger = logging.getLogger(name or "feishurobot")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return setup_logging(name)
