"""
auth.py
Authentication Blueprint – register, login, logout.
"""

from datetime import datetime, timedelta

from flask import (Blueprint, flash, redirect, render_template,
                   request, url_for)
from flask_login import current_user, login_required, login_user, logout_user

from models import Organisation, User, db

auth = Blueprint("auth", __name__)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

@auth.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    errors = {}
    form   = {}

    if request.method == "POST":
        form = {
            "org_name":         request.form.get("org_name", "").strip(),
            "name":             request.form.get("name", "").strip(),
            "email":            request.form.get("email", "").strip().lower(),
            "password":         request.form.get("password", ""),
            "confirm_password": request.form.get("confirm_password", ""),
        }

        if not form["org_name"]:
            errors["org_name"] = "Company name is required."
        if not form["name"]:
            errors["name"] = "Your name is required."
        if not form["email"] or "@" not in form["email"]:
            errors["email"] = "A valid email address is required."
        elif User.query.filter_by(email=form["email"]).first():
            errors["email"] = "An account with this email already exists."
        if len(form["password"]) < 8:
            errors["password"] = "Password must be at least 8 characters."
        elif form["password"] != form["confirm_password"]:
            errors["confirm_password"] = "Passwords do not match."

        if not errors:
            org = Organisation(
                name=form["org_name"],
                plan="trial",
                trial_ends_at=datetime.utcnow() + timedelta(days=14),
            )
            db.session.add(org)
            db.session.flush()   # materialise org.id before user FK

            user = User(org_id=org.id, email=form["email"], name=form["name"], role="admin")
            user.set_password(form["password"])
            db.session.add(user)
            db.session.commit()

            login_user(user, remember=True)
            return redirect(url_for("dashboard.index"))

    return render_template("register.html", errors=errors, form=form)


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@auth.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    error = None

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        remember = bool(request.form.get("remember"))

        user = User.query.filter_by(email=email).first()
        if not user or not user.check_password(password):
            error = "Invalid email or password."
        elif not user.organisation.is_active:
            error = "Your trial has expired. Please upgrade to continue."
        else:
            login_user(user, remember=remember)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.index"))

    return render_template("login.html", error=error)


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

@auth.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
