"""
utils.py
Date helpers and general-purpose utilities used across the pipeline.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from config import DISPLAY_DATE_FORMAT, FIXED_EXPIRY_WINDOW_DAYS

logger = logging.getLogger(__name__)


def setup_logging(level: int = logging.INFO) -> None:
    """Configure root logger with a timestamped format."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_date(value: str) -> Optional[date]:
    """
    Try common date formats and return a date object, or None on failure.
    Accepts: YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD/MM/YY.
    """
    if not value or str(value).strip().lower() in ("", "nan", "none", "nat"):
        return None

    raw = str(value).strip()
    formats = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y")
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue

    logger.warning("Could not parse date: %r", raw)
    return None


def is_within_days(target: Optional[date], window_days: int = FIXED_EXPIRY_WINDOW_DAYS) -> bool:
    """Return True if *target* is between today and today + window_days (inclusive)."""
    if target is None:
        return False
    today = date.today()
    return today <= target <= today + timedelta(days=window_days)


def format_date(d: Optional[date]) -> str:
    """Format a date for display; return empty string for None."""
    if d is None:
        return ""
    return d.strftime(DISPLAY_DATE_FORMAT)


def today_str(fmt: str = DISPLAY_DATE_FORMAT) -> str:
    return date.today().strftime(fmt)


def safe_float(value, default: Optional[float] = None) -> Optional[float]:
    """Cast *value* to float; return *default* on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
