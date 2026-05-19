"""
processor.py
All business logic for the refi-opportunity pipeline.

Responsibilities:
  - LVR calculation and banding
  - Rate, timing, and confidence flags
  - Priority scoring
  - Opportunity and recommended-action labels
  - Building the enriched output row dict
"""

import logging
from datetime import date
from typing import Optional

import config
from utils import format_date, is_within_days, parse_date, safe_float, today_str

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Labels (centralised so they are easy to swap for future localisation)
# ---------------------------------------------------------------------------
BAND_STRONG   = "✅ Strong Refi"
BAND_REVIEW   = "⚠ Review"
BAND_NO_REFI  = "❌ No Refi"

FLAG_RATE     = "💰 Above Market"
FLAG_EXPIRY   = "⏰ Expiring Soon"
FLAG_LOW_CONF = "⚠ Low Confidence – Order Val"

ACTION_CALL    = "Call Client"
ACTION_ASSESS  = "Assess Options"
ACTION_MONITOR = "Monitor"

AVM_UNAVAILABLE = "AVM Unavailable"


def calculate_lvr(loan_balance: float, avm_value: float) -> Optional[float]:
    """Return LVR as a decimal (e.g. 0.75 for 75 %). None if avm_value is zero."""
    if avm_value <= 0:
        logger.warning("AVM value is zero or negative (%s) – cannot calculate LVR.", avm_value)
        return None
    return loan_balance / avm_value


def band_lvr(lvr: Optional[float]) -> str:
    """Map a decimal LVR to a human-readable band label."""
    if lvr is None:
        return AVM_UNAVAILABLE
    if lvr <= config.LVR_STRONG_THRESHOLD:
        return BAND_STRONG
    if lvr <= config.LVR_REVIEW_THRESHOLD:
        return BAND_REVIEW
    return BAND_NO_REFI


def rate_flag(current_rate: Optional[float]) -> str:
    if current_rate is not None and current_rate >= config.RATE_THRESHOLD:
        return FLAG_RATE
    return ""


def timing_flag(fixed_expiry_date: Optional[date]) -> str:
    if is_within_days(fixed_expiry_date):
        return FLAG_EXPIRY
    return ""


def confidence_flag(confidence: Optional[float]) -> str:
    if (
        confidence is not None
        and confidence < config.AVM_LOW_CONFIDENCE_THRESHOLD
    ):
        return FLAG_LOW_CONF
    return ""


def priority_score(
    lvr: Optional[float],
    current_rate: Optional[float],
    fixed_expiry_date: Optional[date],
) -> int:
    """
    Numeric score used to sort rows by refi urgency (higher = more urgent).
    """
    score = 0

    if lvr is not None:
        if lvr <= config.LVR_STRONG_THRESHOLD:
            score += config.SCORE_STRONG_LVR
        elif lvr <= config.LVR_REVIEW_THRESHOLD:
            score += config.SCORE_REVIEW_LVR

    if current_rate is not None and current_rate >= config.RATE_THRESHOLD:
        score += config.SCORE_HIGH_RATE

    if is_within_days(fixed_expiry_date):
        score += config.SCORE_EXPIRY_SOON

    return score


def recommended_action(lvr_band: str) -> str:
    mapping = {
        BAND_STRONG:  ACTION_CALL,
        BAND_REVIEW:  ACTION_ASSESS,
        BAND_NO_REFI: ACTION_MONITOR,
    }
    return mapping.get(lvr_band, ACTION_MONITOR)


def process_row(raw: dict, avm_result: Optional[dict]) -> dict:
    """
    Accept a raw CSV row dict and an AVM result (or None) and return a
    fully enriched output row dict ready for DataFrame construction.
    """
    # --- Parse inputs ---------------------------------------------------
    loan_balance  = safe_float(raw.get("loan_balance"))
    current_rate  = safe_float(raw.get("current_rate"))
    fixed_expiry  = parse_date(str(raw.get("fixed_expiry", "")))

    # --- AVM fields -----------------------------------------------------
    if avm_result:
        avm_value  = avm_result.get("value")
        avm_low    = avm_result.get("low")
        avm_high   = avm_result.get("high")
        avm_conf   = avm_result.get("confidence")
        avm_date   = avm_result.get("date", "")
    else:
        avm_value = avm_low = avm_high = avm_conf = None
        avm_date  = ""

    # --- Core calculations ----------------------------------------------
    lvr      = calculate_lvr(loan_balance, avm_value) if (loan_balance and avm_value) else None
    lvr_band = band_lvr(lvr)

    r_flag   = rate_flag(current_rate)
    t_flag   = timing_flag(fixed_expiry)
    c_flag   = confidence_flag(avm_conf)
    score    = priority_score(lvr, current_rate, fixed_expiry)
    action   = recommended_action(lvr_band)

    # --- Confidence display (convert 0-1 to percentage string) ----------
    conf_display = f"{avm_conf:.0%}" if avm_conf is not None else ""

    return {
        "Client ID":          raw.get("client_id", ""),
        "Client Name":        raw.get("client_name", ""),
        "Broker":             raw.get("broker", ""),
        "Address":            raw.get("address", ""),
        "Estimated Value":    avm_value,
        "Low Range":          avm_low,
        "High Range":         avm_high,
        "Confidence":         conf_display,
        "Loan Balance":       loan_balance,
        "LVR":                round(lvr, 4) if lvr is not None else None,
        "LVR Band":           lvr_band,
        "Rate":               current_rate,
        "Rate Flag":          r_flag,
        "Timing Flag":        t_flag,
        "Confidence Flag":    c_flag,
        "Opportunity":        lvr_band,
        "Recommended Action": action,
        "Priority Score":     score,
        "AVM Date":           avm_date,
        "Run Date":           today_str(),
        "Disclaimer":         config.DISCLAIMER,
        # Internal sort key – not written to Excel
        "_fixed_expiry_date": format_date(fixed_expiry),
    }
