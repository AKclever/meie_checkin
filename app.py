import os
from datetime import date, timedelta
from functools import wraps
from pathlib import Path

from flask import abort, Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# --- APP & DB CONFIG ---
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me")

# hoidke sqlite fail hosti kettal (hiljem Dockeris mountime /app/data)
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DATA_DIR / 'checkin.db'}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# --- MODELS ---
class User(db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(50), unique=True, nullable=False)  # "mina", "tema" vms
    password_hash = db.Column(db.String(200), nullable=False)

    checkins = db.relationship("CheckIn", backref="user", lazy=True, cascade="all, delete-orphan")

    def set_password(self, password: str):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class Question(db.Model):
    __tablename__ = "question"
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(255), nullable=False)
    kind = db.Column(db.String(20), nullable=False, default="text")  # "scale" või "text"

    answers = db.relationship("Answer", backref="question", lazy=True, cascade="all, delete-orphan")


class CheckIn(db.Model):
    __tablename__ = "check_in"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    week_start = db.Column(db.Date, nullable=False)

    answers = db.relationship("Answer", backref="checkin", lazy=True, cascade="all, delete-orphan")


class Answer(db.Model):
    __tablename__ = "answer"
    id = db.Column(db.Integer, primary_key=True)
    checkin_id = db.Column(db.Integer, db.ForeignKey("check_in.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("question.id"), nullable=False)
    value = db.Column(db.Text, nullable=False)


# --- HELPERS ---
def get_current_week_start() -> date:
    """Tagasta jooksva nädala esmaspäev."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday


def current_user():
    uid = session.get("user_id")
    return User.query.get(uid) if uid else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_user():
    return {"user": current_user()}


def is_admin(user):
    return user and user.slug == "mina"


# --- CLI: DB INIT / SEED ---
@app.cli.command("init-db")
def init_db():
    """Käivita:  FLASK_APP=app.py flask init-db   (Windows: set FLASK_APP=app.py)"""
    db.drop_all()
    db.create_all()

    # 2 kasutajat (muuda nimed/paroolid hiljem!)
    a = User(name="Mina", slug="mina"); a.set_password("parool2")
    b = User(name="Tema", slug="tema"); b.set_password("parool1")
    db.session.add_all([a, b])

    # vaikimisi küsimused
    q1 = Question(text="Kui lähedaseks sa meid sel nädalal tunned? (1–10)", kind="scale")
    q2 = Question(text="Kui rahul oled suhtlusega sel nädalal? (1–10)", kind="scale")
    q3 = Question(text="Mille eest tahaksid partnerile sel nädalal aitäh öelda?", kind="text")
    q4 = Question(text="Mis võiks järgmisel nädalal parem olla?", kind="text")
    db.session.add_all([q1, q2, q3, q4])

    db.session.commit()
    print("OK: andmebaas loodud ja algandmed sisestatud.")


# --- ROUTES ---
@app.route("/")
def index():
    return render_template("index.html", user=current_user())


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        slug = request.form.get("slug", "").strip()
        password = request.form.get("password", "").strip()
        user = User.query.filter_by(slug=slug).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            flash(f"Tere, {user.name}!", "success")
            return redirect(url_for("dashboard"))
        flash("Vale kasutaja või parool.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    flash("Logitud välja.", "info")
    return redirect(url_for("login"))


@app.route("/checkin", methods=["GET", "POST"])
@login_required
def checkin():
    user = current_user()
    week_start = get_current_week_start()
    questions = Question.query.order_by(Question.id.asc()).all()

    previous = (
        CheckIn.query
        .filter(CheckIn.user_id == user.id)
        .order_by(CheckIn.week_start.desc())
        .first()
    )
    prev_answers = {}
    if previous:
        for a in previous.answers:
            prev_answers[a.question_id] = a.value

    if request.method == "POST":
        ch = CheckIn(user_id=user.id, week_start=week_start)
        db.session.add(ch)
        db.session.flush()  # et ch.id olemas oleks

        for q in questions:
            raw = request.form.get(f"q_{q.id}", "").strip()
            if raw:
                db.session.add(Answer(checkin_id=ch.id, question_id=q.id, value=raw))

        db.session.commit()
        flash("Check-in salvestatud!", "success")
        return redirect(url_for("dashboard"))

    return render_template("checkin.html",
                           user=user,
                           questions=questions,
                           week_start=week_start,
                           prev_answers=prev_answers)


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    checkins = (CheckIn.query
                .filter_by(user_id=user.id)
                .order_by(CheckIn.week_start.desc())
                .all())

    def calculate_streak(checkins):
        if not checkins:
            return 0
        weeks = sorted({c.week_start for c in checkins})
        streak = 1
        for i in range(len(weeks) - 1, 0, -1):
            diff = (weeks[i] - weeks[i - 1]).days
            if diff == 7:
                streak += 1
            else:
                break
        return streak

    scale_q = Question.query.filter_by(kind="scale").first()
    labels, values = [], []
    if scale_q:
        for ch in sorted(checkins, key=lambda c: c.week_start):
            ans = next((a for a in ch.answers if a.question_id == scale_q.id), None)
            if ans:
                labels.append(ch.week_start.strftime("%Y-%m-%d"))
                try:
                    values.append(int(ans.value))
                except ValueError:
                    values.append(None)

    streak = calculate_streak(checkins)

    return render_template("dashboard.html",
                           user=user,
                           checkins=checkins,
                           chart_labels=labels,
                           chart_values=values,
                           scale_question=scale_q,
                           streak=streak)


@app.route("/week/<int:checkin_id>")
@login_required
def week_detail(checkin_id):
    ch = CheckIn.query.get_or_404(checkin_id)
    user = current_user()
    if ch.user_id != user.id:
        abort(403)
    return render_template("week_detail.html", checkin=ch)


# BONUS: kahe kasutaja ühine graafik (esimese skaalaküsimuse järgi)
@app.route("/couple")
@login_required
def couple():
    users = User.query.order_by(User.id.asc()).all()
    scale_q = Question.query.filter_by(kind="scale").first()
    if not scale_q or len(users) < 2:
        flash("Kahe kasutaja vaade eeldab vähemalt kahte kasutajat ja üht skaalaküsimust.", "info")
        return redirect(url_for("dashboard"))

    all_weeks = sorted({ch.week_start for u in users for ch in u.checkins})
    labels = [d.strftime("%Y-%m-%d") for d in all_weeks]

    series = {}
    for u in users:
        week_to_val = {}
        for ch in u.checkins:
            ans = next((a for a in ch.answers if a.question_id == scale_q.id), None)
            if ans:
                try:
                    week_to_val[ch.week_start] = int(ans.value)
                except ValueError:
                    week_to_val[ch.week_start] = None
        series[u.name] = [week_to_val.get(w, None) for w in all_weeks]

    return render_template("couple.html", labels=labels, series=series)


@app.route("/admin/questions", methods=["GET", "POST"])
@login_required
def admin_questions():
    user = current_user()
    if not is_admin(user):
        abort(403)

    if request.method == "POST":
        text = request.form.get("text", "").strip()
        kind = request.form.get("kind", "text")
        if text:
            db.session.add(Question(text=text, kind=kind))
            db.session.commit()
            flash("Küsimus lisatud.", "success")
        return redirect(url_for("admin_questions"))

    questions = Question.query.order_by(Question.id.asc()).all()
    return render_template("admin_questions.html", questions=questions)


if __name__ == "__main__":
    # arenduses mugav (prodiks kasutame gunicorn'i)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
