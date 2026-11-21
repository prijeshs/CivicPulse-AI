import os, re, secrets
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import func

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-this-secret"),
    SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///civicresolve.db").replace("postgres://", "postgresql://"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)
db = SQLAlchemy(app)
csrf = CSRFProtect(app)

CATEGORIES = {
    "Roads & Transport": ["road", "pothole", "streetlight", "traffic", "bus", "bridge", "footpath"],
    "Water Supply": ["water", "leak", "pipeline", "drinking", "sewage", "drainage"],
    "Electricity": ["electricity", "power", "wire", "transformer", "outage", "voltage"],
    "Sanitation": ["garbage", "waste", "rubbish", "clean", "toilet", "mosquito", "drain"],
    "Public Safety": ["danger", "accident", "fire", "open manhole", "unsafe", "emergency"],
    "Other": [],
}
DEPARTMENTS = {
    "Roads & Transport": "Public Works / Transport Department",
    "Water Supply": "Municipal Water Board",
    "Electricity": "Electricity Board",
    "Sanitation": "Municipal Sanitation Department",
    "Public Safety": "Public Safety Department",
    "Other": "Citizen Services Helpdesk",
}
URGENT_TERMS = {"urgent", "emergency", "danger", "accident", "fire", "injury", "exposed wire", "flood", "open manhole"}

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(160), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="citizen")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Grievance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tracking_id = db.Column(db.String(16), unique=True, nullable=False, index=True)
    citizen_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=False)
    location = db.Column(db.String(180), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    department = db.Column(db.String(100), nullable=False)
    priority = db.Column(db.String(20), nullable=False)
    priority_reason = db.Column(db.String(180), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="Submitted")
    officer_note = db.Column(db.String(500), default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    citizen = db.relationship("User", backref="grievances")

def analyze_grievance(title, description):
    """Transparent, deterministic triage; no external AI/API call is claimed."""
    text = f"{title} {description}".lower()
    scores = {category: sum(1 for term in terms if term in text) for category, terms in CATEGORIES.items()}
    category = max(scores, key=scores.get)
    if scores[category] == 0:
        category = "Other"
    urgent_hits = sorted(term for term in URGENT_TERMS if term in text)
    if urgent_hits:
        priority, reason = "Critical", "Urgency indicator detected: " + ", ".join(urgent_hits[:3])
    elif any(word in text for word in ("many", "entire area", "multiple", "community", "overflow")):
        priority, reason = "High", "Potential impact on multiple residents or a shared service"
    else:
        priority, reason = "Normal", "No explicit emergency indicator detected"
    return category, DEPARTMENTS[category], priority, reason

def current_user():
    uid = session.get("user_id")
    return db.session.get(User, uid) if uid else None

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def officer_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if user.role != "officer":
            abort(403)
        return fn(*args, **kwargs)
    return wrapper

@app.context_processor
def inject_globals():
    return {"current_user": current_user(), "now": datetime.now(timezone.utc)}

@app.route("/")
def index():
    stats = {
        "total": Grievance.query.count(),
        "resolved": Grievance.query.filter_by(status="Resolved").count(),
        "pending": Grievance.query.filter(Grievance.status != "Resolved").count(),
    }
    recent = Grievance.query.order_by(Grievance.created_at.desc()).limit(5).all()
    return render_template("index.html", stats=stats, recent=recent)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if len(name) < 2 or len(name) > 80 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid name and email address.", "danger")
        elif len(password) < 10:
            flash("Password must contain at least 10 characters.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "danger")
        else:
            user = User(name=name, email=email, password_hash=generate_password_hash(password), role="citizen")
            db.session.add(user); db.session.commit()
            session.clear(); session["user_id"] = user.id
            flash("Account created. You can now submit and track grievances.", "success")
            return redirect(url_for("dashboard"))
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            session.clear(); session["user_id"] = user.id
            flash("Welcome back.", "success")
            return redirect(url_for("officer_dashboard" if user.role == "officer" else "dashboard"))
        flash("Invalid email or password.", "danger")
    return render_template("auth.html", mode="login")

@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    items = Grievance.query.filter_by(citizen_id=user.id).order_by(Grievance.created_at.desc()).all()
    return render_template("dashboard.html", items=items)

@app.route("/grievances/new", methods=["GET", "POST"])
@login_required
def new_grievance():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        location = request.form.get("location", "").strip()
        if not (5 <= len(title) <= 120 and 20 <= len(description) <= 3000 and 5 <= len(location) <= 180):
            flash("Check your inputs: title 5–120 characters, description 20–3000, location 5–180.", "danger")
        else:
            category, department, priority, reason = analyze_grievance(title, description)
            tracking_id = "CR-" + secrets.token_hex(16).upper()
            item = Grievance(tracking_id=tracking_id, citizen_id=current_user().id,
                title=title, description=description, location=location, category=category,
                department=department, priority=priority, priority_reason=reason)
            db.session.add(item); db.session.commit()
            flash(f"Grievance submitted. Your tracking ID is {tracking_id}.", "success")
            return redirect(url_for("grievance_detail", tracking_id=tracking_id))
    return render_template("new_grievance.html")

@app.route("/track", methods=["GET", "POST"])
def track():
    item = None
    if request.method == "POST":
        tracking_id = request.form.get("tracking_id", "").strip().upper()
        item = Grievance.query.filter_by(tracking_id=tracking_id).first()
        if not item:
            flash("No grievance found with that tracking ID.", "warning")
    return render_template("track.html", item=item)

@app.route("/grievances/<tracking_id>")
@login_required
def grievance_detail(tracking_id):
    item = Grievance.query.filter_by(tracking_id=tracking_id.upper()).first_or_404()
    user = current_user()
    if user.role != "officer" and item.citizen_id != user.id:
        abort(403)
    return render_template("detail.html", item=item)

@app.route("/officer")
@officer_required
def officer_dashboard():
    status = request.args.get("status", "")
    query = Grievance.query
    if status in {"Submitted", "Under Review", "In Progress", "Resolved"}:
        query = query.filter_by(status=status)
    items = query.order_by(Grievance.created_at.desc()).all()
    counts = {s: Grievance.query.filter_by(status=s).count() for s in ["Submitted", "Under Review", "In Progress", "Resolved"]}
    return render_template("officer.html", items=items, counts=counts, selected=status)

@app.post("/officer/grievances/<tracking_id>/update")
@officer_required
def update_grievance(tracking_id):
    item = Grievance.query.filter_by(tracking_id=tracking_id.upper()).first_or_404()
    status = request.form.get("status", "")
    note = request.form.get("officer_note", "").strip()
    allowed = {"Submitted", "Under Review", "In Progress", "Resolved"}
    if status not in allowed or len(note) > 500:
        flash("Invalid status or note.", "danger")
    else:
        item.status = status
        item.officer_note = note
        db.session.commit()
        flash("Grievance record updated.", "success")
    return redirect(url_for("grievance_detail", tracking_id=item.tracking_id))

@app.cli.command("create-officer")
def create_officer():
    """Create an officer account: flask --app app create-officer"""
    import getpass
    email = input("Officer email: ").strip().lower()
    name = input("Officer name: ").strip()
    password = getpass.getpass("Password (10+ chars): ")
    if len(password) < 10 or User.query.filter_by(email=email).first():
        print("Password too short or email already exists."); return
    db.session.add(User(name=name, email=email, password_hash=generate_password_hash(password), role="officer"))
    db.session.commit()
    print("Officer account created.")

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)
