import logging
import os
import sys


def get_logger(name: str) -> logging.Logger:
    """Return a pipeline logger with optional debug output."""
    logger = logging.getLogger(f"pipeline.{name}")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG if os.getenv("PIPELINE_VERBOSE") == "1" else logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger