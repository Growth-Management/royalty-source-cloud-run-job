from __future__ import annotations

import logging
import os
import sys

from app.pipeline import SourcePipeline
from app.settings import Settings


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )


def main() -> int:
    configure_logging(os.getenv("LOG_LEVEL", "INFO"))
    logger = logging.getLogger(__name__)
    try:
        settings = Settings.from_env()
        configure_logging(settings.log_level)
        stats = SourcePipeline(settings).run()
        logger.info("pipeline completed", extra={"run_id": stats.run_id, "raw_rows_loaded": stats.raw_rows_loaded})
        return 0
    except Exception:
        logger.exception("pipeline failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
