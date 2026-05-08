"""Настройка логирования для всего приложения."""

import logging
import os
import sys


def setup(level: str = None) -> None:
    """Настраивает корневой логгер с консольным хендлером.

    Args:
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR).
               По умолчанию берётся из переменной LOG_LEVEL или INFO.
    """
    log_level = level or os.getenv("LOG_LEVEL", "INFO").upper()

    formatter = logging.Formatter(
        fmt="[%(asctime)s] %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))

    # Убираем дублирующие хендлеры при повторном вызове
    if not root.handlers:
        root.addHandler(handler)
