import logging
from rich.logging import RichHandler

def setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)]
    )
    # Silence third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("pyzotero").setLevel(logging.WARNING)

def get_logger(name: str):
    return logging.getLogger(name)
