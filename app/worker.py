"""Worker entrypoint.

Milestone 1 placeholder: it starts, logs, and idles until it is asked to stop, so the
`worker` service can already run under Docker Compose. The RQ worker arrives in Milestone 4.
"""

import logging
import signal
import threading
from types import FrameType

from app.config import get_settings
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    stop = threading.Event()

    def request_stop(signum: int, _frame: FrameType | None) -> None:
        logger.info("worker stopping", extra={"signal": signal.Signals(signum).name})
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    logger.info("worker started (placeholder, no jobs processed yet)")
    stop.wait()
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
