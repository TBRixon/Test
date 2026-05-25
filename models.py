"""
models.py
SQLAlchemy ORM models for RefiSight.

Tables:
  organisations – one per broker firm (tenant root)
  users         – login accounts, scoped to an organisation
  clients       – MyCRM loan records, scoped to an organisation
"""

from datetime import datetime, timedelta

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


class Organisation(db.Model):
    __tablename__ = "organisations"

    id             = db.Column(db.Integer, primary_key=True)
    name           = db.Column(db.String(200), nullable=False)
    plan           = db.Column(db.String(50), default="trial")   # trial | starter | growth | enterprise
    trial_ends_at  = db.Column(db.DateTime, nullable=True)
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    users   = db.relationship("User",   backref="organisation", lazy=True, cascade="all, delete-orphan")
    clients = db.relationship("Client", backref="organisation", lazy=True, cascade="all, delete-orphan")

    @property
    def is_trial(self) -> bool:
        return self.plan == "trial"

    @property
    def trial_days_remaining(self) -> int:
        if not self.trial_ends_at:
            return 0
        return max(0, (self.trial_ends_at - datetime.utcnow()).days)

    @property
    def is_active(self) -> bool:
        if self.plan == "trial":
            return bool(self.trial_ends_at and datetime.utcnow() < self.trial_ends_at)
        return self.plan in ("starter", "growth", "enterprise")

    def trial_badge(self) -> dict | None:
        """Return badge info for the frontend, or None if not on trial."""
        if not self.is_trial:
            return None
        days = self.trial_days_remaining
        if days <= 3:
            return {"label": f"{days}d left", "style": "danger"}
        if days <= 7:
            return {"label": f"{days}d left", "style": "warning"}
        return {"label": f"Trial – {days}d left", "style": "info"}


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id            = db.Column(db.Integer, primary_key=True)
    org_id        = db.Column(db.Integer, db.ForeignKey("organisations.id"), nullable=False)
    email         = db.Column(db.String(200), unique=True, nullable=False)
    name          = db.Column(db.String(200), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role          = db.Column(db.String(50), default="admin")   # admin | broker
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Client(db.Model):
    __tablename__ = "clients"
    __table_args__ = (
        db.UniqueConstraint("org_id", "client_id", name="uq_org_client"),
    )

    id           = db.Column(db.Integer, primary_key=True)
    org_id       = db.Column(db.Integer, db.ForeignKey("organisations.id"), nullable=False)
    client_id    = db.Column(db.String(100), nullable=False)   # MyCRM ID
    client_name  = db.Column(db.String(200), default="")
    address      = db.Column(db.Text, default="")
    loan_balance = db.Column(db.String(50), default="")
    current_rate = db.Column(db.String(20), default="")
    broker       = db.Column(db.String(100), default="")
    fixed_expiry = db.Column(db.String(50), default="")
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "client_id":    self.client_id,
            "client_name":  self.client_name,
            "address":      self.address,
            "loan_balance": self.loan_balance,
            "current_rate": self.current_rate,
            "broker":       self.broker,
            "fixed_expiry": self.fixed_expiry,
        }
