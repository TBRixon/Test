"""
dashboard.py
Dashboard Blueprint – all authenticated routes.

Every route requires login. All DB queries are scoped to current_user.org_id.
"""

from pathlib import Path

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

dashboard = Blueprint("dashboard", __name__)


def _range_str(r: dict) -> str:
    lo = r.get("Low Range")
    hi = r.get("High Range")
    if lo and hi:
        return f"${lo:,.0f} – ${hi:,.0f}"
    return "—"


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@dashboard.route("/")
@login_required
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@dashboard.route("/upload", methods=["POST"])
@login_required
def upload():
    from app import invalidate_cache, rebuild_address_map
    from database import process_upload

    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (".csv", ".xlsx", ".xls"):
        return jsonify({"error": "Unsupported file type. Please upload .csv or .xlsx."}), 400

    try:
        result = process_upload(file, current_user.org_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422

    invalidate_cache(current_user.org_id)
    rebuild_address_map(current_user.org_id)
    return jsonify(result)


# ---------------------------------------------------------------------------
# API – data
# ---------------------------------------------------------------------------

@dashboard.route("/api/data")
@login_required
def api_data():
    from app import get_cached_rows

    rows = get_cached_rows(current_user.org_id)
    out  = []
    for r in rows:
        lvr = r.get("LVR")
        avm = r.get("Estimated Value")
        out.append({
            "client_id":       r.get("Client ID"),
            "client_name":     r.get("Client Name"),
            "broker":          r.get("Broker"),
            "address":         r.get("Address"),
            "avm_value":       f"${avm:,.0f}" if avm else "—",
            "avm_range":       _range_str(r),
            "confidence":      r.get("Confidence") or "—",
            "loan_balance":    f"${r.get('Loan Balance'):,.0f}" if r.get("Loan Balance") else "—",
            "lvr":             f"{lvr:.1%}" if lvr is not None else "—",
            "lvr_raw":         round(lvr * 100, 1) if lvr is not None else None,
            "lvr_band":        r.get("LVR Band"),
            "rate":            f"{r.get('Rate'):.2f}%" if r.get("Rate") else "—",
            "rate_flag":       r.get("Rate Flag"),
            "timing_flag":     r.get("Timing Flag"),
            "confidence_flag": r.get("Confidence Flag"),
            "action":          r.get("Recommended Action"),
            "score":           r.get("Priority Score", 0),
            "avm_date":        r.get("AVM Date") or "—",
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return jsonify(out)


# ---------------------------------------------------------------------------
# API – summary
# ---------------------------------------------------------------------------

@dashboard.route("/api/summary")
@login_required
def api_summary():
    from app import get_cached_rows

    rows    = get_cached_rows(current_user.org_id)
    brokers = sorted({r.get("Broker", "") for r in rows if r.get("Broker")})
    return jsonify({
        "total":      len(rows),
        "strong":     sum(1 for r in rows if "Strong"      in str(r.get("LVR Band", ""))),
        "review":     sum(1 for r in rows if "Review"      in str(r.get("LVR Band", ""))),
        "no_refi":    sum(1 for r in rows if "No Refi"     in str(r.get("LVR Band", ""))),
        "unavail":    sum(1 for r in rows if "Unavailable" in str(r.get("LVR Band", ""))),
        "above_rate": sum(1 for r in rows if r.get("Rate Flag")),
        "expiring":   sum(1 for r in rows if r.get("Timing Flag")),
        "brokers":    brokers,
        "run_date":   rows[0].get("Run Date", "") if rows else "",
    })


# ---------------------------------------------------------------------------
# API – current user / org profile (used by frontend nav)
# ---------------------------------------------------------------------------

@dashboard.route("/api/me")
@login_required
def api_me():
    org   = current_user.organisation
    badge = org.trial_badge()
    return jsonify({
        "user_name": current_user.name,
        "user_email": current_user.email,
        "org_name":  org.name,
        "plan":      org.plan,
        "trial_badge": badge,
    })
