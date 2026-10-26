import logging
import sys
from pythonjsonlogger import jsonlogger
from app.core.config import settings


def setup_logging() -> None:
    """Configures structured JSON logging across all backend processes."""
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # Clear existing handlers
    if logger.hasHandlers():
        logger.handlers.clear()

    log_handler = logging.StreamHandler(sys.stdout)
    formatter = jsonlogger.JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(module)s %(message)s"
    )
    log_handler.setFormatter(formatter)
    logger.addHandler(log_handler)


def get_logger(name: str) -> logging.Logger:
    """Convenience getter for a named logger instance."""
    return logging.getLogger(name)
