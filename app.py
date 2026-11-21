
import os
import re
import secrets
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, abort, g
)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import func, case
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash


# =========================================================
# APPLICATION CONFIGURATION
# =========================================================

app = Flask(__name__)

database_url = os.environ.get(
    "DATABASE_URL",
    "sqlite:///civicpulse.db"
)

# Render and other providers may supply postgres:// URLs.
if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://",
        "postgresql+psycopg://",
        1
    )
elif database_url.startswith("postgresql://"):
    database_url = database_url.replace(
        "postgresql://",
        "postgresql+psycopg://",
        1
)

app.config.update(
    SECRET_KEY=os.environ.get(
        "SECRET_KEY",
        "development-only-change-this-secret"
    ),

    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,

    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=(
        os.environ.get("COOKIE_SECURE", "0") == "1"
    ),

    PERMANENT_SESSION_LIFETIME=3600,

    MAX_CONTENT_LENGTH=1 * 1024 * 1024,

    SQLALCHEMY_ENGINE_OPTIONS={
        "pool_pre_ping": True,
    },
)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)


# =========================================================
# GRIEVANCE CLASSIFICATION
# =========================================================

CATEGORIES = {
    "Roads & Transport": [
        "road", "pothole", "streetlight", "traffic",
        "bus", "bridge", "footpath"
    ],

    "Water Supply": [
        "water", "leak", "pipeline", "drinking",
        "sewage", "drainage"
    ],

    "Electricity": [
        "electricity", "power", "wire",
        "transformer", "outage", "voltage"
    ],

    "Sanitation": [
        "garbage", "waste", "rubbish",
        "clean", "toilet", "mosquito", "drain"
    ],

    "Public Safety": [
        "danger", "accident", "fire",
        "open manhole", "unsafe", "emergency"
    ],

    "Other": []
}

DEPARTMENTS = {
    "Roads & Transport":
        "Public Works / Transport Department",

    "Water Supply":
        "Municipal Water Board",

    "Electricity":
        "Electricity Board",

    "Sanitation":
        "Municipal Sanitation Department",

    "Public Safety":
        "Public Safety Department",

    "Other":
        "Citizen Services Helpdesk"
}

URGENT_TERMS = {
    "urgent",
    "emergency",
    "danger",
    "accident",
    "fire",
    "injury",
    "exposed wire",
    "flood",
    "open manhole"
}

STATUSES = (
    "Submitted",
    "Under Review",
    "In Progress",
    "Resolved"
)


def analyze_grievance(title, description):
    """
    Transparent, deterministic grievance classification.
    This is rule-based triage, not a live LLM integration.
    """

    text = f"{title} {description}".lower()

    scores = {
        category: sum(
            1 for keyword in keywords
            if keyword in text
        )
        for category, keywords in CATEGORIES.items()
    }

    category = max(scores, key=scores.get)

    if scores[category] == 0:
        category = "Other"

    urgent_hits = sorted(
        term for term in URGENT_TERMS
        if term in text
    )

    if urgent_hits:
        priority = "Critical"
        reason = (
            "Urgency indicator detected: "
            + ", ".join(urgent_hits[:3])
        )

    elif any(
        word in text
        for word in (
            "many",
            "entire area",
            "multiple",
            "community",
            "overflow"
        )
    ):
        priority = "High"
        reason = (
            "Potential impact on multiple residents "
            "or a shared service"
        )

    else:
        priority = "Normal"
        reason = (
            "No explicit emergency indicator detected"
        )

    department = DEPARTMENTS[category]

    return category, department, priority, reason


# =========================================================
# DATABASE MODELS
# =========================================================

class User(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(80),
        nullable=False
    )

    email = db.Column(
        db.String(160),
        unique=True,
        nullable=False,
        index=True
    )

    password_hash = db.Column(
        db.String(255),
        nullable=False
    )

    role = db.Column(
        db.String(20),
        nullable=False,
        default="citizen"
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class Grievance(db.Model):

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    tracking_id = db.Column(
        db.String(16),
        unique=True,
        nullable=False,
        index=True
    )

    citizen_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True
    )

    title = db.Column(
        db.String(120),
        nullable=False
    )

    description = db.Column(
        db.Text,
        nullable=False
    )

    location = db.Column(
        db.String(180),
        nullable=False
    )

    category = db.Column(
        db.String(50),
        nullable=False
    )

    department = db.Column(
        db.String(100),
        nullable=False
    )

    priority = db.Column(
        db.String(20),
        nullable=False
    )

    priority_reason = db.Column(
        db.String(180),
        nullable=False
    )

    status = db.Column(
        db.String(30),
        nullable=False,
        default="Submitted",
        index=True
    )

    officer_note = db.Column(
        db.String(500),
        default=""
    )

    created_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )

    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )

    citizen = db.relationship(
        "User",
        backref="grievances"
    )

    __table_args__ = (
        db.Index(
            "ix_grievance_citizen_created",
            "citizen_id",
            "created_at"
        ),
        db.Index(
            "ix_grievance_status_created",
            "status",
            "created_at"
        ),
    )


# =========================================================
# USER AUTHENTICATION
# =========================================================

def current_user():

    if not hasattr(g, "_current_user"):

        uid = session.get("user_id")

        g._current_user = (
            db.session.get(User, uid)
            if uid is not None
            else None
        )

    return g._current_user


def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if not current_user():

            flash(
                "Please sign in to continue.",
                "warning"
            )

            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return wrapper


def officer_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        user = current_user()

        if not user:

            flash(
                "Please sign in to continue.",
                "warning"
            )

            return redirect(url_for("login"))

        if user.role != "officer":
            abort(403)

        return function(*args, **kwargs)

    return wrapper


@app.context_processor
def inject_globals():

    return {
        "current_user": current_user(),
        "now": datetime.now(timezone.utc)
    }


# =========================================================
# HOMEPAGE
# =========================================================

@app.route("/")
def index():

    total, resolved, pending = db.session.query(

        func.count(Grievance.id),

        func.coalesce(
            func.sum(
                case(
                    (Grievance.status == "Resolved", 1),
                    else_=0
                )
            ),
            0
        ),

        func.coalesce(
            func.sum(
                case(
                    (Grievance.status != "Resolved", 1),
                    else_=0
                )
            ),
            0
        )

    ).one()

    stats = {
        "total": total,
        "resolved": resolved,
        "pending": pending
    }

    recent = (
        Grievance.query
        .order_by(Grievance.created_at.desc())
        .limit(5)
        .all()
    )

    return render_template(
        "index.html",
        stats=stats,
        recent=recent
    )


# =========================================================
# REGISTRATION
# =========================================================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form.get(
            "name", ""
        ).strip()

        email = request.form.get(
            "email", ""
        ).strip().lower()

        password = request.form.get(
            "password", ""
        )

        valid_email = re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            email
        )

        if not (2 <= len(name) <= 80) or not valid_email:

            flash(
                "Enter a valid name and email address.",
                "danger"
            )

        elif len(password) < 10:

            flash(
                "Password must contain at least 10 characters.",
                "danger"
            )

        elif User.query.filter_by(email=email).first():

            flash(
                "An account with that email already exists.",
                "danger"
            )

        else:

            user = User(
                name=name,
                email=email,
                password_hash=generate_password_hash(password),
                role="citizen"
            )

            try:

                db.session.add(user)
                db.session.commit()

                session.clear()
                session["user_id"] = user.id

                flash(
                    "Account created successfully.",
                    "success"
                )

                return redirect(
                    url_for("dashboard")
                )

            except IntegrityError:

                db.session.rollback()

                flash(
                    "This email is already registered.",
                    "danger"
                )

    return render_template(
        "auth.html",
        mode="register"
    )


# =========================================================
# LOGIN
# =========================================================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form.get(
            "email", ""
        ).strip().lower()

        password = request.form.get(
            "password", ""
        )

        user = User.query.filter_by(
            email=email
        ).first()

        if user and check_password_hash(
            user.password_hash,
            password
        ):

            session.clear()
            session["user_id"] = user.id

            flash(
                "Welcome back.",
                "success"
            )

            destination = (
                "officer_dashboard"
                if user.role == "officer"
                else "dashboard"
            )

            return redirect(
                url_for(destination)
            )

        flash(
            "Invalid email or password.",
            "danger"
        )

    return render_template(
        "auth.html",
        mode="login"
    )


# =========================================================
# LOGOUT
# =========================================================

@app.post("/logout")
@login_required
def logout():

    session.clear()

    flash(
        "You have been signed out.",
        "success"
    )

    return redirect(
        url_for("index")
    )


# =========================================================
# CITIZEN DASHBOARD
# =========================================================

@app.route("/dashboard")
@login_required
def dashboard():

    user = current_user()

    items = (
        Grievance.query
        .filter_by(citizen_id=user.id)
        .order_by(Grievance.created_at.desc())
        .all()
    )

    return render_template(
        "dashboard.html",
        items=items
    )


# =========================================================
# CREATE GRIEVANCE
# =========================================================

@app.route(
    "/grievances/new",
    methods=["GET", "POST"]
)
@login_required
def new_grievance():

    if request.method == "POST":

        title = request.form.get(
            "title", ""
        ).strip()

        description = request.form.get(
            "description", ""
        ).strip()

        location = request.form.get(
            "location", ""
        ).strip()

        valid = (
            5 <= len(title) <= 120
            and 20 <= len(description) <= 3000
            and 5 <= len(location) <= 180
        )

        if not valid:

            flash(
                "Check your inputs: title 5–120 characters, "
                "description 20–3000, location 5–180.",
                "danger"
            )

        else:

            category, department, priority, reason = (
                analyze_grievance(title, description)
            )

            tracking_id = (
                "CR-" + secrets.token_hex(16).upper()
            )

            item = Grievance(
                tracking_id=tracking_id,
                citizen_id=current_user().id,
                title=title,
                description=description,
                location=location,
                category=category,
                department=department,
                priority=priority,
                priority_reason=reason
            )

            try:

                db.session.add(item)
                db.session.commit()

                flash(
                    f"Grievance submitted. "
                    f"Your tracking ID is {tracking_id}.",
                    "success"
                )

                return redirect(
                    url_for(
                        "grievance_detail",
                        tracking_id=tracking_id
                    )
                )

            except IntegrityError:

                db.session.rollback()

                flash(
                    "Unable to submit the grievance. Please try again.",
                    "danger"
                )

    return render_template(
        "new_grievance.html"
    )


# =========================================================
# PUBLIC GRIEVANCE TRACKING
# =========================================================

@app.route(
    "/track",
    methods=["GET", "POST"]
)
def track():

    item = None

    if request.method == "POST":

        tracking_id = request.form.get(
            "tracking_id", ""
        ).strip().upper()

        if tracking_id:

            item = Grievance.query.filter_by(
                tracking_id=tracking_id
            ).first()

        if not item:

            flash(
                "No grievance found with that tracking ID.",
                "warning"
            )

    return render_template(
        "track.html",
        item=item
    )


# =========================================================
# GRIEVANCE DETAILS
# =========================================================

@app.route(
    "/grievances/<tracking_id>"
)
@login_required
def grievance_detail(tracking_id):

    item = Grievance.query.filter_by(
        tracking_id=tracking_id.upper()
    ).first_or_404()

    user = current_user()

    if (
        user.role != "officer"
        and item.citizen_id != user.id
    ):
        abort(403)

    return render_template(
        "detail.html",
        item=item
    )


# =========================================================
# OFFICER DASHBOARD
# =========================================================

@app.route("/officer")
@officer_required
def officer_dashboard():

    status = request.args.get(
        "status", ""
    )

    query = Grievance.query

    if status in STATUSES:

        query = query.filter_by(
            status=status
        )

    items = (
        query
        .order_by(Grievance.created_at.desc())
        .all()
    )

    # One grouped query instead of four separate count queries.
    rows = (
        db.session.query(
            Grievance.status,
            func.count(Grievance.id)
        )
        .group_by(Grievance.status)
        .all()
    )

    counts = {
        current_status: 0
        for current_status in STATUSES
    }

    counts.update({
        current_status: count
        for current_status, count in rows
        if current_status in counts
    })

    return render_template(
        "officer.html",
        items=items,
        counts=counts,
        selected=status
    )


# =========================================================
# UPDATE GRIEVANCE STATUS
# =========================================================

@app.post(
    "/officer/grievances/<tracking_id>/update"
)
@officer_required
def update_grievance(tracking_id):

    item = Grievance.query.filter_by(
        tracking_id=tracking_id.upper()
    ).first_or_404()

    status = request.form.get(
        "status", ""
    )

    note = request.form.get(
        "officer_note", ""
    ).strip()

    if (
        status not in STATUSES
        or len(note) > 500
    ):

        flash(
            "Invalid status or note.",
            "danger"
        )

    else:

        item.status = status
        item.officer_note = note

        try:

            db.session.commit()

            flash(
                "Grievance record updated.",
                "success"
            )

        except Exception:

            db.session.rollback()

            app.logger.exception(
                "Failed to update grievance"
            )

            flash(
                "Unable to update the grievance. Please try again.",
                "danger"
            )

    return redirect(
        url_for(
            "grievance_detail",
            tracking_id=item.tracking_id
        )
    )


# =========================================================
# DATABASE INITIALIZATION COMMAND
# =========================================================

@app.cli.command("init-db")
def init_db():

    """
    Initialize database tables.

    Run once before starting Gunicorn.
    """

    db.create_all()

    print("CivicPulse-AI database initialized successfully.")


# =========================================================
# CREATE OFFICER ACCOUNT
# =========================================================

@app.cli.command("create-officer")
def create_officer():

    import getpass

    email = input(
        "Officer email: "
    ).strip().lower()

    name = input(
        "Officer name: "
    ).strip()

    password = getpass.getpass(
        "Password (10+ characters): "
    )

    if not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        email
    ):

        print("Invalid email address.")
        return

    if not (2 <= len(name) <= 80):

        print("Invalid officer name.")
        return

    if len(password) < 10:

        print("Password must contain at least 10 characters.")
        return

    if User.query.filter_by(email=email).first():

        print("An account with this email already exists.")
        return

    officer = User(
        name=name,
        email=email,
        password_hash=generate_password_hash(password),
        role="officer"
    )

    db.session.add(officer)

    try:

        db.session.commit()

        print("Officer account created successfully.")

    except IntegrityError:

        db.session.rollback()

        print("Could not create officer account.")


# =========================================================
# LOCAL DEVELOPMENT
# =========================================================

if __name__ == "__main__":

    with app.app_context():
        db.create_all()

    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        debug=False
    )
