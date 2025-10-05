import logging
import structlog


def setup_logging():
    logging.basicConfig(
        format="%(message)s",
        stream=None,
        level=logging.INFO,
    )
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )