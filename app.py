"""
app.py
Flask dashboard server for the MyCRM Refi Opportunity pipeline.

Serves the dashboard UI and provides a JSON API that the frontend
consumes. When COTALITY_API_KEY is not set, realistic mock AVM data
is used so the dashboard works out of the box for demonstration.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

# Patch AVMClient to use mock data when no API key is configured
import avm_client as _avm_module

# Mock AVM values keyed by client_id – realistic Brisbane property data
MOCK_AVM_DATA = {
    "12345": {"value": 750000,  "low": 710000,  "high": 795000,  "confidence": 0.88, "date": "2025-05-01"},
    "12346": {"value": 680000,  "low": 645000,  "high": 715000,  "confidence": 0.91, "date": "2025-05-01"},
    "12347": {"value": 1040000, "low": 980000,  "high": 1100000, "confidence": 0.82, "date": "2025-05-01"},
    "12348": {"value": 520000,  "low": 495000,  "high": 545000,  "confidence": 0.93, "date": "2025-05-01"},
    "12349": {"value": 820000,  "low": 775000,  "high": 865000,  "confidence": 0.86, "date": "2025-05-01"},
    "12350": {"value": 710000,  "low": 670000,  "high": 750000,  "confidence": 0.89, "date": "2025-05-01"},
    "12351": {"value": 480000,  "low": 450000,  "high": 510000,  "confidence": 0.84, "date": "2025-05-01"},
    "12352": None,
    "12353": {"value": 350000,  "low": 315000,  "high": 385000,  "confidence": 0.55, "date": "2025-05-01"},
    "12354": {"value": 720000,  "low": 685000,  "high": 755000,  "confidence": 0.87, "date": "2025-05-01"},
}

# address (normalised) → client_id, used by the mock AVM fetch
_address_to_client_id: dict[str, str] = {}


def _mock_fetch(self, address: str):
    cid = _address_to_client_id.get(_avm_module.AVMClient._normalise_address(address), "")
    return MOCK_AVM_DATA.get(cid)


if not os.getenv("COTALITY_API_KEY"):
    _avm_module.AVMClient._fetch_with_retry = _mock_fetch  # type: ignore[method-assign]

from database import get_all_clients, init_db, process_upload, seed_from_csv
from processor import process_row
from utils import setup_logging

setup_logging(logging.WARNING)

app = Flask(__name__)

DEFAULT_CSV = Path(__file__).parent / "sample_data.csv"

# Module-level AVM client singleton – shared cache across all requests
_avm_client = _avm_module.AVMClient()

# Processed-row cache – invalidated whenever the DB changes
_cached_rows: list[dict] | None = None


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def _rebuild_address_map() -> None:
    """Sync _address_to_client_id from the DB so the mock AVM lookup works."""
    for client in get_all_clients():
        addr = _avm_module.AVMClient._normalise_address(str(client.get("address", "")))
        _address_to_client_id[addr] = str(client.get("client_id", ""))


with app.app_context():
    init_db()
    seed_from_csv(DEFAULT_CSV)
    _rebuild_address_map()


# ---------------------------------------------------------------------------
# Data pipeline
# ---------------------------------------------------------------------------

def _build_rows() -> list[dict]:
    rows = []
    for client in get_all_clients():
        address = str(client.get("address", "")).strip()
        avm = _avm_client.get_avm(address)
        rows.append(process_row(client, avm))
    return rows


def get_rows() -> list[dict]:
    global _cached_rows
    if _cached_rows is None:
        _cached_rows = _build_rows()
    return _cached_rows


def _invalidate_cache() -> None:
    global _cached_rows
    _cached_rows = None
    _avm_client._cache.clear()
    _rebuild_address_map()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected."}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (".csv", ".xlsx", ".xls"):
        return jsonify({"error": "Unsupported file type. Please upload .csv or .xlsx."}), 400

    try:
        result = process_upload(file)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422

    _invalidate_cache()
    return jsonify(result)


@app.route("/api/data")
def api_data():
    rows = get_rows()
    out = []
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


@app.route("/api/summary")
def api_summary():
    rows = get_rows()
    brokers = sorted({r.get("Broker", "") for r in rows if r.get("Broker")})
    return jsonify({
        "total":       len(rows),
        "strong":      sum(1 for r in rows if "Strong"      in str(r.get("LVR Band", ""))),
        "review":      sum(1 for r in rows if "Review"      in str(r.get("LVR Band", ""))),
        "no_refi":     sum(1 for r in rows if "No Refi"     in str(r.get("LVR Band", ""))),
        "unavail":     sum(1 for r in rows if "Unavailable" in str(r.get("LVR Band", ""))),
        "above_rate":  sum(1 for r in rows if r.get("Rate Flag")),
        "expiring":    sum(1 for r in rows if r.get("Timing Flag")),
        "brokers":     brokers,
        "run_date":    rows[0].get("Run Date", "") if rows else "",
    })


def _range_str(r: dict) -> str:
    lo = r.get("Low Range")
    hi = r.get("High Range")
    if lo and hi:
        return f"${lo:,.0f} – ${hi:,.0f}"
    return "—"


if __name__ == "__main__":
    app.run(debug=True, port=5000)
