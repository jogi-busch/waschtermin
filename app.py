"""Waschtermin – Terminplattform für Waschstraßen und Autofahrer.

Waschstraßen zahlen ein monatliches Abo und verwalten ihre freien Termine
ausschließlich hier. Autofahrer suchen und buchen freie Termine.
"""
import os
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, abort, flash, g, has_request_context, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from i18n import LANG_NAMES, LANGS, TRANSLATIONS, WEEKDAYS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-nur-lokal-aendern"),
    DATABASE=os.environ.get("DATABASE", os.path.join(BASE_DIR, "waschtermin.db")),
    # Monatsbeitrag für Waschstraßen in Cent
    MONTHLY_FEE_CENTS=int(os.environ.get("MONTHLY_FEE_CENTS", "5900")),
    # "demo" = Zahlung wird nur simuliert. Für Echtbetrieb Zahlungsanbieter anbinden (siehe README).
    PAYMENT_MODE=os.environ.get("PAYMENT_MODE", "demo"),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

# Anzeige des Monatsbeitrags in der Währung der Sprache (nur zur Orientierung, abgerechnet wird in Euro).
# Kurse vom 25.10.2026; bei Bedarf hier anpassen: (Zeichen, Einheiten pro 1 EUR, Rundung, Symbol vorne?)
FEE_CURRENCIES = {
    "kk": ("₸", 505.16, 100, False),
    "ru": ("₽", 90.28, 10, False),
    "en": ("$", 1.1202, 1, True),
}

SERVICES = ["Basiswäsche", "Komfortwäsche", "Premiumwäsche", "Innenreinigung", "Handwäsche"]


# --------------------------------------------------------------------------
# Datenbank
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('wash', 'driver')),
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS washes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id),
    name TEXT NOT NULL,
    street TEXT NOT NULL,
    zip TEXT NOT NULL,
    city TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    subscription_until TEXT
);
CREATE TABLE IF NOT EXISTS slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wash_id INTEGER NOT NULL REFERENCES washes(id),
    start_time TEXT NOT NULL,
    duration_min INTEGER NOT NULL,
    service TEXT NOT NULL,
    price_cents INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_slots_start ON slots(start_time);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id INTEGER NOT NULL REFERENCES slots(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    car_note TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('confirmed', 'cancelled_by_driver', 'cancelled_by_wash')),
    created_at TEXT NOT NULL
);
-- Ein Slot kann höchstens eine aktive Buchung haben (verhindert Doppelbuchungen).
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_booking
    ON bookings(slot_id) WHERE status = 'confirmed';
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(app.config["DATABASE"])
    db.executescript(SCHEMA)
    db.commit()
    db.close()


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------
def now():
    return datetime.now().replace(second=0, microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def parse_dt(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M")


@app.template_filter("euro")
def euro(cents):
    if g.get("lang") == "en":
        return f"€{cents / 100:,.2f}"
    return f"{cents / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def tr(key, **kw):
    """Übersetzt einen Schlüssel in die Sprache der aktuellen Anfrage (Standard: Deutsch)."""
    lang = g.get("lang", "de") if has_request_context() else "de"
    entry = TRANSLATIONS[key]
    text = entry.get(lang) or entry["de"]
    return text.format(**kw) if kw else text


def reset_session():
    lang = session.get("lang")
    session.clear()
    if lang in LANGS:
        session["lang"] = lang


@app.template_filter("feeprice")
def fee_price(cents):
    """Monatsbeitrag: in Deutsch als Euro, sonst als gerundeter Näherungswert in der Landeswährung."""
    lang = g.get("lang", "de")
    if lang not in FEE_CURRENCIES:
        return euro(cents)
    symbol, rate, step, before = FEE_CURRENCIES[lang]
    value = int(round(cents / 100 * rate / step)) * step
    num = f"{value:,}".replace(",", "\u00a0")
    return f"≈\u00a0{symbol}{num}" if before else f"≈\u00a0{num}\u00a0{symbol}"


@app.template_filter("dt")
def fmt_dt(value):
    d = parse_dt(value)
    wd = WEEKDAYS[g.get("lang", "de")][d.weekday()]
    return f"{wd}, {d.strftime('%d.%m.%Y')} · {d.strftime('%H:%M')}{tr('time.suffix')}"


@app.template_filter("datefmt")
def fmt_date(value):
    return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%d.%m.%Y")


@app.template_filter("svc")
def fmt_service(name):
    key = "svc." + name
    return tr(key) if key in TRANSLATIONS else name


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["t"] = tr
app.jinja_env.globals["LANG_NAMES"] = LANG_NAMES


@app.context_processor
def inject_hero_photo():
    """Wenn static/hero.jpg existiert, wird sie statt der Illustration im Kopfbereich gezeigt."""
    return {"hero_photo": os.path.exists(os.path.join(BASE_DIR, "static", "hero.jpg")),
            "lang": g.get("lang", "de")}
app.jinja_env.globals["SERVICES"] = SERVICES


@app.before_request
def set_language_for_request():
    lang = session.get("lang", "de")
    g.lang = lang if lang in LANGS else "de"


@app.route("/sprache/<code>")
def set_language(code):
    if code in LANGS:
        session["lang"] = code
    nxt = request.args.get("next", "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    return redirect(nxt)


@app.before_request
def csrf_protect():
    if request.method == "POST":
        sent = request.form.get("csrf", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, tr("err.csrf"))


@app.before_request
def load_user():
    g.user = None
    g.wash = None
    uid = session.get("uid")
    if uid:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if g.user is None:
            reset_session()
        elif g.user["role"] == "wash":
            g.wash = get_db().execute(
                "SELECT * FROM washes WHERE user_id = ?", (uid,)
            ).fetchone()


def subscription_active(wash):
    until = wash["subscription_until"]
    return bool(until) and parse_dt(until) >= now()


app.jinja_env.globals["subscription_active"] = subscription_active


def login_required(role=None):
    def deco(view):
        @wraps(view)
        def wrapped(*a, **kw):
            if g.user is None:
                flash(tr("flash.login_required"), "error")
                return redirect(url_for("login", next=request.path))
            if role and g.user["role"] != role:
                abort(403)
            return view(*a, **kw)
        return wrapped
    return deco


def parse_price(text):
    text = text.strip().replace("€", "").replace(" ", "").replace(",", ".")
    value = round(float(text) * 100)
    if value < 0 or value > 100000:
        raise ValueError
    return value


# --------------------------------------------------------------------------
# Öffentliche Seiten & Authentifizierung
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html", fee=app.config["MONTHLY_FEE_CENTS"])


@app.route("/registrieren/<role>", methods=["GET", "POST"])
def register(role):
    if role not in ("wash", "driver"):
        abort(404)
    if request.method == "POST":
        f = request.form
        email = f.get("email", "").strip().lower()
        name = f.get("name", "").strip()
        password = f.get("password", "")
        errors = []
        if "@" not in email or len(email) > 200:
            errors.append(tr("flash.bad_email"))
        if not name:
            errors.append(tr("flash.no_name"))
        if len(password) < 8:
            errors.append(tr("flash.short_pw"))
        wash_data = {}
        if role == "wash":
            wash_data = {k: f.get(k, "").strip() for k in ("street", "zip", "city", "description")}
            if not (wash_data["street"] and wash_data["zip"] and wash_data["city"]):
                errors.append(tr("flash.no_address"))
        db = get_db()
        if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            errors.append(tr("flash.email_taken"))
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("register.html", role=role, form=f), 400
        cur = db.execute(
            "INSERT INTO users (email, password_hash, role, name, created_at) VALUES (?,?,?,?,?)",
            (email, generate_password_hash(password), role, name, iso(now())),
        )
        if role == "wash":
            db.execute(
                "INSERT INTO washes (user_id, name, street, zip, city, description) VALUES (?,?,?,?,?,?)",
                (cur.lastrowid, name, wash_data["street"], wash_data["zip"],
                 wash_data["city"], wash_data["description"]),
            )
        db.commit()
        reset_session()
        session["uid"] = cur.lastrowid
        flash(tr("flash.welcome"), "ok")
        return redirect(url_for("wash_subscription" if role == "wash" else "search"))
    return render_template("register.html", role=role, form={})


@app.route("/anmelden", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            reset_session()
            session["uid"] = user["id"]
            nxt = request.args.get("next", "")
            if nxt.startswith("/") and not nxt.startswith("//"):
                return redirect(nxt)
            return redirect(url_for("wash_dashboard" if user["role"] == "wash" else "search"))
        flash(tr("flash.bad_login"), "error")
        return render_template("login.html"), 401
    return render_template("login.html")


@app.route("/abmelden", methods=["POST"])
def logout():
    reset_session()
    return redirect(url_for("index"))


# --------------------------------------------------------------------------
# Waschstraßen-Bereich
# --------------------------------------------------------------------------
@app.route("/waschstrasse")
@login_required("wash")
def wash_dashboard():
    db = get_db()
    slots = db.execute(
        """SELECT s.*, b.id AS booking_id, b.car_note, u.name AS driver_name, u.email AS driver_email
           FROM slots s
           LEFT JOIN bookings b ON b.slot_id = s.id AND b.status = 'confirmed'
           LEFT JOIN users u ON u.id = b.user_id
           WHERE s.wash_id = ? AND s.start_time >= ?
           ORDER BY s.start_time""",
        (g.wash["id"], iso(now() - timedelta(hours=2))),
    ).fetchall()
    return render_template(
        "wash_dashboard.html", slots=slots, active=subscription_active(g.wash),
        today=now().strftime("%Y-%m-%d"),
    )


@app.route("/waschstrasse/termine", methods=["POST"])
@login_required("wash")
def wash_create_slots():
    f = request.form
    try:
        day = datetime.strptime(f["date"], "%Y-%m-%d")
        t_from = datetime.strptime(f["time_from"], "%H:%M")
        t_to = datetime.strptime(f["time_to"], "%H:%M")
        duration = int(f["duration"])
        price = parse_price(f["price"])
        service = f["service"]
    except (KeyError, ValueError):
        flash(tr("flash.fill_all"), "error")
        return redirect(url_for("wash_dashboard"))
    if service not in SERVICES or not (5 <= duration <= 240):
        flash(tr("flash.bad_service"), "error")
        return redirect(url_for("wash_dashboard"))
    start = day.replace(hour=t_from.hour, minute=t_from.minute)
    end = day.replace(hour=t_to.hour, minute=t_to.minute)
    if end <= start:
        flash(tr("flash.bad_range"), "error")
        return redirect(url_for("wash_dashboard"))
    # Wie viele Waschplätze laufen parallel?
    try:
        bays = max(1, min(20, int(f.get("bays", "1"))))
    except ValueError:
        bays = 1
    db = get_db()
    created = 0
    cur = start
    while cur + timedelta(minutes=duration) <= end and created < 200:
        if cur >= now():
            for _ in range(bays):
                db.execute(
                    "INSERT INTO slots (wash_id, start_time, duration_min, service, price_cents) VALUES (?,?,?,?,?)",
                    (g.wash["id"], iso(cur), duration, service, price),
                )
                created += 1
        cur += timedelta(minutes=duration)
    db.commit()
    if created:
        flash(tr("flash.created", n=created), "ok")
    else:
        flash(tr("flash.none_created"), "error")
    return redirect(url_for("wash_dashboard"))


def own_slot_or_404(slot_id):
    slot = get_db().execute(
        "SELECT * FROM slots WHERE id = ? AND wash_id = ?", (slot_id, g.wash["id"])
    ).fetchone()
    if slot is None:
        abort(404)
    return slot


@app.route("/waschstrasse/termine/<int:slot_id>/loeschen", methods=["POST"])
@login_required("wash")
def wash_delete_slot(slot_id):
    own_slot_or_404(slot_id)
    db = get_db()
    booked = db.execute(
        "SELECT 1 FROM bookings WHERE slot_id = ? AND status = 'confirmed'", (slot_id,)
    ).fetchone()
    if booked:
        flash(tr("flash.slot_booked"), "error")
    else:
        db.execute("DELETE FROM bookings WHERE slot_id = ?", (slot_id,))
        db.execute("DELETE FROM slots WHERE id = ?", (slot_id,))
        db.commit()
        flash(tr("flash.slot_deleted"), "ok")
    return redirect(url_for("wash_dashboard"))


@app.route("/waschstrasse/buchungen/<int:booking_id>/stornieren", methods=["POST"])
@login_required("wash")
def wash_cancel_booking(booking_id):
    db = get_db()
    row = db.execute(
        """SELECT b.id FROM bookings b JOIN slots s ON s.id = b.slot_id
           WHERE b.id = ? AND s.wash_id = ? AND b.status = 'confirmed'""",
        (booking_id, g.wash["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    db.execute("UPDATE bookings SET status = 'cancelled_by_wash' WHERE id = ?", (booking_id,))
    db.commit()
    flash(tr("flash.booking_cancelled_wash"), "ok")
    return redirect(url_for("wash_dashboard"))


@app.route("/waschstrasse/profil", methods=["POST"])
@login_required("wash")
def wash_profile():
    f = request.form
    vals = {k: f.get(k, "").strip() for k in ("name", "street", "zip", "city", "description")}
    if not all(vals[k] for k in ("name", "street", "zip", "city")):
        flash(tr("flash.profile_empty"), "error")
    else:
        db = get_db()
        db.execute(
            "UPDATE washes SET name=?, street=?, zip=?, city=?, description=? WHERE id=?",
            (vals["name"], vals["street"], vals["zip"], vals["city"], vals["description"], g.wash["id"]),
        )
        db.commit()
        flash(tr("flash.profile_saved"), "ok")
    return redirect(url_for("wash_dashboard"))


# --- Abo ------------------------------------------------------------------
@app.route("/waschstrasse/abo")
@login_required("wash")
def wash_subscription():
    return render_template(
        "subscription.html", fee=app.config["MONTHLY_FEE_CENTS"],
        active=subscription_active(g.wash), demo=app.config["PAYMENT_MODE"] == "demo",
    )


@app.route("/waschstrasse/abo/zahlen", methods=["POST"])
@login_required("wash")
def wash_pay():
    """Verlängert das Abo um einen Monat.

    Im Demo-Modus wird die Zahlung nur simuliert. Für den Echtbetrieb muss
    hier ein Zahlungsanbieter (z. B. Stripe Checkout/Billing oder Mollie)
    angebunden werden, der erst nach bestätigter Zahlung (Webhook)
    `extend_subscription` aufruft.
    """
    if app.config["PAYMENT_MODE"] != "demo":
        abort(501, tr("err.no_payment"))
    extend_subscription(g.wash["id"])
    flash(tr("flash.sub_extended"), "ok")
    return redirect(url_for("wash_dashboard"))


def extend_subscription(wash_id, days=30):
    db = get_db()
    wash = db.execute("SELECT subscription_until FROM washes WHERE id = ?", (wash_id,)).fetchone()
    base = now()
    if wash["subscription_until"] and parse_dt(wash["subscription_until"]) > base:
        base = parse_dt(wash["subscription_until"])
    db.execute(
        "UPDATE washes SET subscription_until = ? WHERE id = ?",
        (iso(base + timedelta(days=days)), wash_id),
    )
    db.commit()


# --------------------------------------------------------------------------
# Autofahrer-Bereich
# --------------------------------------------------------------------------
@app.route("/suche")
@login_required("driver")
def search():
    q = request.args.get("q", "").strip()
    date = request.args.get("date", "").strip()
    service = request.args.get("service", "").strip()
    sql = """
        SELECT s.*, w.name AS wash_name, w.street, w.zip, w.city, w.description
        FROM slots s
        JOIN washes w ON w.id = s.wash_id
        WHERE s.start_time > ?
          AND w.subscription_until >= ?
          AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.slot_id = s.id AND b.status = 'confirmed')
    """
    params = [iso(now()), iso(now())]
    if q:
        sql += " AND (w.city LIKE ? OR w.zip LIKE ? OR w.name LIKE ?)"
        params += [f"%{q}%"] * 3
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
            sql += " AND substr(s.start_time, 1, 10) = ?"
            params.append(date)
        except ValueError:
            flash(tr("flash.bad_date"), "error")
    if service in SERVICES:
        sql += " AND s.service = ?"
        params.append(service)
    sql += " ORDER BY s.start_time, w.name LIMIT 100"
    slots = get_db().execute(sql, params).fetchall()
    return render_template("search.html", slots=slots, q=q, date=date, service=service,
                           today=now().strftime("%Y-%m-%d"))


@app.route("/termine/<int:slot_id>/buchen", methods=["POST"])
@login_required("driver")
def book(slot_id):
    db = get_db()
    slot = db.execute(
        """SELECT s.*, w.subscription_until FROM slots s JOIN washes w ON w.id = s.wash_id
           WHERE s.id = ?""", (slot_id,)
    ).fetchone()
    if (slot is None or slot["start_time"] <= iso(now())
            or not slot["subscription_until"] or parse_dt(slot["subscription_until"]) < now()):
        flash(tr("flash.unavailable"), "error")
        return redirect(url_for("search"))
    try:
        db.execute(
            "INSERT INTO bookings (slot_id, user_id, car_note, created_at) VALUES (?,?,?,?)",
            (slot_id, g.user["id"], request.form.get("car_note", "").strip()[:100], iso(now())),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash(tr("flash.taken"), "error")
        return redirect(url_for("search"))
    flash(tr("flash.booked"), "ok")
    return redirect(url_for("my_bookings"))


@app.route("/meine-termine")
@login_required("driver")
def my_bookings():
    rows = get_db().execute(
        """SELECT b.id, b.status, b.car_note, s.start_time, s.duration_min, s.service, s.price_cents,
                  w.name AS wash_name, w.street, w.zip, w.city
           FROM bookings b JOIN slots s ON s.id = b.slot_id JOIN washes w ON w.id = s.wash_id
           WHERE b.user_id = ? ORDER BY s.start_time DESC""",
        (g.user["id"],),
    ).fetchall()
    return render_template("my_bookings.html", rows=rows, now=iso(now()))


@app.route("/meine-termine/<int:booking_id>/stornieren", methods=["POST"])
@login_required("driver")
def cancel_booking(booking_id):
    db = get_db()
    row = db.execute(
        """SELECT b.id, s.start_time FROM bookings b JOIN slots s ON s.id = b.slot_id
           WHERE b.id = ? AND b.user_id = ? AND b.status = 'confirmed'""",
        (booking_id, g.user["id"]),
    ).fetchone()
    if row is None:
        abort(404)
    if row["start_time"] <= iso(now()):
        flash(tr("flash.past_cancel"), "error")
    else:
        db.execute("UPDATE bookings SET status = 'cancelled_by_driver' WHERE id = ?", (booking_id,))
        db.commit()
        flash(tr("flash.cancelled"), "ok")
    return redirect(url_for("my_bookings"))


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
