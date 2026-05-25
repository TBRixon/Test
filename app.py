"""
app.py
RefiSight Flask application – multi-tenant SaaS entry point.

Architecture:
  - SQLAlchemy (SQLite locally, PostgreSQL in prod via DATABASE_URL)
  - Flask-Login for session auth
  - Per-org row cache keyed by org_id
  - auth Blueprint  (/login, /register, /logout)
  - dashboard Blueprint  (/, /upload, /api/*)
"""

import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from flask_login import LoginManager

load_dotenv()

# ---------------------------------------------------------------------------
# AVM mock patch (applied before any import that uses AVMClient)
# ---------------------------------------------------------------------------
import avm_client as _avm_module

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

# address → client_id, populated per-org for the mock lookup
_address_to_client_id: dict[str, str] = {}


def _mock_fetch(self, address: str):
    cid = _address_to_client_id.get(_avm_module.AVMClient._normalise_address(address), "")
    return MOCK_AVM_DATA.get(cid)


if not os.getenv("COTALITY_API_KEY"):
    _avm_module.AVMClient._fetch_with_retry = _mock_fetch  # type: ignore[method-assign]

# Module-level AVM singleton (in-memory cache shared across orgs; safe because
# AVM values are address-based, not tenant-specific)
_avm_client = _avm_module.AVMClient()

# ---------------------------------------------------------------------------
# Per-org processed-row cache
# ---------------------------------------------------------------------------
_rows_cache: dict[int, list[dict]] = {}


def get_cached_rows(org_id: int) -> list[dict]:
    if org_id not in _rows_cache:
        _rows_cache[org_id] = _build_rows(org_id)
    return _rows_cache[org_id]


def invalidate_cache(org_id: int) -> None:
    _rows_cache.pop(org_id, None)


def _build_rows(org_id: int) -> list[dict]:
    from database import get_all_clients
    from processor import process_row

    rows = []
    for client in get_all_clients(org_id):
        address = str(client.get("address", "")).strip()
        avm = _avm_client.get_avm(address)
        rows.append(process_row(client, avm))
    return rows


def rebuild_address_map(org_id: int) -> None:
    from database import get_all_clients
    for client in get_all_clients(org_id):
        addr = _avm_module.AVMClient._normalise_address(str(client.get("address", "")))
        _address_to_client_id[addr] = str(client.get("client_id", ""))


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> Flask:
    from utils import setup_logging
    setup_logging(logging.WARNING)

    app = Flask(__name__)

    # Secret key – use env var in production, random fallback for dev
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or secrets.token_hex(32)

    # Database URL – SQLite locally, PostgreSQL in production
    db_url = os.getenv("DATABASE_URL", "sqlite:///refi_pipeline.db")
    if db_url.startswith("postgres://"):          # Heroku/Railway compat
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"]        = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Init extensions
    from models import db
    db.init_app(app)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view     = "auth.login"   # type: ignore[assignment]
    login_manager.login_message  = "Please sign in to continue."

    from models import User

    @login_manager.user_loader
    def load_user(user_id: str):
        return User.query.get(int(user_id))

    # Register blueprints
    from auth import auth as auth_bp
    from dashboard import dashboard as dashboard_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)

    # Create tables + seed demo data
    with app.app_context():
        db.create_all()
        _seed_demo_org(app)

    return app


def _seed_demo_org(app: Flask) -> None:
    """
    Create a demo organisation + admin user on first run so the app
    works immediately without going through registration.
    """
    from models import Organisation, User, db

    if Organisation.query.count() > 0:
        return

    from datetime import datetime, timedelta
    from database import seed_from_csv

    demo_org = Organisation(
        name="The Brokerage (Demo)",
        plan="trial",
        trial_ends_at=datetime.utcnow() + timedelta(days=14),
    )
    db.session.add(demo_org)
    db.session.flush()

    demo_user = User(org_id=demo_org.id, email="demo@refisight.com.au", name="Demo Admin", role="admin")
    demo_user.set_password("demo1234")
    db.session.add(demo_user)
    db.session.commit()

    default_csv = Path(app.root_path) / "sample_data.csv"
    seed_from_csv(demo_org.id, default_csv)
    rebuild_address_map(demo_org.id)

    app.logger.info(
        "Demo org created – login: demo@refisight.com.au / demo1234"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

app = create_app()

if __name__ == "__main__":
    port  = int(os.getenv("PORT", 5000))
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
