"""
config.py
Central configuration for the MyCRM → Cotality AVM → Refi Opportunity pipeline.
All thresholds and environment variable names live here.
"""

import os

# ---------------------------------------------------------------------------
# LVR thresholds
# ---------------------------------------------------------------------------
LVR_STRONG_THRESHOLD = 0.80      # <= 80 %  → Strong Refi candidate
LVR_REVIEW_THRESHOLD = 0.90      # 80–90 %  → Review
                                  # > 90 %   → No Refi

# ---------------------------------------------------------------------------
# Rate threshold (percentage points, e.g. 6.20 == 6.20 %)
# ---------------------------------------------------------------------------
RATE_THRESHOLD = 6.20

# ---------------------------------------------------------------------------
# Fixed-rate expiry window
# ---------------------------------------------------------------------------
FIXED_EXPIRY_WINDOW_DAYS = 180   # Flag loans expiring within 6 months

# ---------------------------------------------------------------------------
# Priority scoring weights
# ---------------------------------------------------------------------------
SCORE_STRONG_LVR = 3
SCORE_REVIEW_LVR = 1
SCORE_HIGH_RATE   = 2
SCORE_EXPIRY_SOON = 2

# ---------------------------------------------------------------------------
# AVM confidence threshold – below this we flag for a formal valuation
# ---------------------------------------------------------------------------
AVM_LOW_CONFIDENCE_THRESHOLD = 0.70   # 70 %

# ---------------------------------------------------------------------------
# API / network settings (values loaded from environment at runtime)
# ---------------------------------------------------------------------------
COTALITY_BASE_URL: str = os.getenv("COTALITY_BASE_URL", "https://api.cotality.com.au/v1")
COTALITY_API_KEY:  str = os.getenv("COTALITY_API_KEY", "")

AVM_ENDPOINT = "/property/avm"          # appended to COTALITY_BASE_URL

# Retry settings for HTTP calls
API_MAX_RETRIES     = 4
API_BACKOFF_BASE_S  = 2                 # seconds; doubles each retry
API_TIMEOUT_S       = 15               # per-request timeout

# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
OUTPUT_DATE_FORMAT  = "%Y%m%d"
DISPLAY_DATE_FORMAT = "%d/%m/%Y"

DISCLAIMER = (
    "AVM is an estimate only and not a formal valuation. "
    "Use for internal servicing purposes only."
)

# ---------------------------------------------------------------------------
# Column ordering for the Master sheet
# ---------------------------------------------------------------------------
MASTER_COLUMNS = [
    "Client ID",
    "Client Name",
    "Broker",
    "Address",
    "Estimated Value",
    "Low Range",
    "High Range",
    "Confidence",
    "Loan Balance",
    "LVR",
    "LVR Band",
    "Rate",
    "Rate Flag",
    "Timing Flag",
    "Confidence Flag",
    "Opportunity",
    "Recommended Action",
    "Priority Score",
    "AVM Date",
    "Run Date",
    "Disclaimer",
]
