"""Centralised logging setup: JSON file logs (hourly rotation) + Sentry."""

import json
import logging
import os
import traceback
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f"
            )[:-3]
            + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = "".join(traceback.format_exception(*record.exc_info)).strip()
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, ensure_ascii=False)


def _hourly_namer(default_name: str) -> str:
    """Rename rotated files to YYYY-MM-DD_HH.log instead of app.log.YYYY-MM-DD_HH."""
    # default_name = "<base_path>.YYYY-MM-DD_HH"
    base, suffix = default_name.rsplit(".", 1)
    log_dir = Path(base).parent
    return str(log_dir / f"{suffix}.log")


def setup_logging(log_dir: str, sentry_dsn: str = "", log_level: str = "INFO") -> None:
    """Configure root logger: JSON file (hourly) + plain console + optional Sentry."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any handlers added by basicConfig or third-party libs before ours
    root.handlers.clear()

    # --- Console handler (human-readable) ---
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s")
    )
    root.addHandler(console)

    # --- Hourly rotating JSON file handler ---
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # Base filename: logs/app.log  →  rotated to logs/YYYY-MM-DD_HH.log
    base_file = log_path / "app.log"
    file_handler = TimedRotatingFileHandler(
        filename=str(base_file),
        when="h",
        interval=1,
        backupCount=720,   # 30 days × 24 h
        encoding="utf-8",
        utc=True,
    )
    file_handler.namer = _hourly_namer
    file_handler.setLevel(level)
    file_handler.setFormatter(_JsonFormatter())
    root.addHandler(file_handler)

    # Silence noisy third-party loggers at WARNING unless debug
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # --- Sentry (optional) ---
    if sentry_dsn:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration
            from sentry_sdk.integrations.fastapi import FastApiIntegration
            from sentry_sdk.integrations.starlette import StarletteIntegration

            sentry_sdk.init(
                dsn=sentry_dsn,
                integrations=[
                    LoggingIntegration(
                        level=logging.WARNING,       # breadcrumbs from WARNING+
                        event_level=logging.ERROR,   # send event on ERROR+
                    ),
                    StarletteIntegration(transaction_style="endpoint"),
                    FastApiIntegration(transaction_style="endpoint"),
                ],
                traces_sample_rate=0.1,   # 10% performance traces
                send_default_pii=False,
            )
            logging.getLogger(__name__).info("Sentry initialised")
        except ImportError:
            logging.getLogger(__name__).warning(
                "sentry-sdk not installed — Sentry disabled. Run: pip install sentry-sdk"
            )
