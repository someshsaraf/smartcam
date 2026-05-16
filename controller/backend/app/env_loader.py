"""Load ``controller/backend/.env`` before the app reads ``os.environ``."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = _BACKEND_ROOT / ".env"


def load_dotenv_file() -> bool:
    """
    Load variables from ``.env`` if present.

    Existing shell environment wins (``override=False``).
    """
    if not ENV_FILE.is_file():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError as e:
        logger.warning(
            "python-dotenv not installed; cannot load %s (%s). "
            "pip install -r requirements.txt",
            ENV_FILE,
            e,
        )
        return False
    load_dotenv(ENV_FILE, override=False)
    logger.debug("loaded environment from %s", ENV_FILE)
    return True


load_dotenv_file()
