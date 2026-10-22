import os
import hmac
import time
import secrets
import sqlite3
import json
import re
from urllib.parse import urlencode
from pathlib import Path

import requests
from flask import Flask, redirect, request, session, jsonify, send_from_directory, abort

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "")
API = "https://discord.com/api/v10"
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "")
GUILD_ID = os.environ.get("GUILD_ID", "").strip()
TICKET_STAFF_ROLE_ID = os.environ.get("TICKET_STAFF_ROLE_ID", "").strip()
PANEL_ADMIN_IDS = {x.strip() for x in os.environ.get("PANEL_ADMIN_IDS", "").split(",") if x.strip().isdigit()}
PANEL_ADMIN_IDS -= {"773243438673952839"}
BOT_API_URL = os.environ.get("BOT_API_URL", "").strip().rstrip("/")
BOT_API_SECRET = os.environ.get("API_SECRET_KEY", "").strip()

app.config["MAX_CONTENT_LENGTH"] = 300 * 1024
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
TICKET_CATEGORIES = {"clan", "partner", "question", "report", "other"}
MAX_TICKETS = 500
MAX_STAFF = 500
SESSION_MAX_UNVERIFIED = 3600
STAFF_FRESH_SECONDS = 25 * 60

DB_PATH = os.environ.get("DB_PATH", "/data/falcons.db")
try:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
except Exception:
    DB_PATH = "./falcons.db"

MSG_NO_ROLE = "Доступ запрещён. Для входа в панель FALCONS необходима роль TICKET STAFF. Обратись к администрации."
MSG_NOT_IN_GUILD = "Доступ запрещён: ты не состоишь на Discord-сервере FALCONS."
MSG_UNAVAILABLE = "Проверка роли временно недоступна. Попробуй позже."
MSG_NOT_CONFIGURED = "Проверка ролей не настроена. Обратись к администрации."
MSG_REVOKED = "Доступ закрыт: у тебя больше нет роли TICKET STAFF."
MSG_RECHECK = "Для безопасности нужно войти заново."

@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp

def _csrf_token():
    tok = session.get("csrf_token")
    if not tok:
        tok = secrets.token_hex(32)
        session["csrf_token"] = tok
    return tok

def _csrf_ok():
    given = request.headers.get("X-CSRF-Token", "")
    return bool(given) and hmac.compare_digest(given, session.get("csrf_token", ""))

MACHINE_ENDPOINTS = (
    "/api/update-stats",
    "/api/update-staff",
    "/api/update-tickets",
    "/api/update-invites",
    "/api/log",
)

_ACTION_COOLDOWNS = {"/api/wipe": 30, "/api/punish": 10, "/api/meeting": 10}
_last_action: dict = {}

@app.before_request
def _cooldown_guard():
    if request.method != "POST":
        return None
    cooldown = _ACTION_COOLDOWNS.get(request.path)
    if not cooldown:
        return None
    user = session.get("user") or {}
    uid = str(user.get("id") or request.remote_addr or "?")
    key = (uid, request.path)
    now = time.monotonic()
    last = _last_action.get(key)
    if last is not None and now - last < cooldown:
        return jsonify({"error": "Слишком часто. Подожди несколько секунд и попробуй снова."}), 429
    _last_action[key] = now
    return None

@app.before_request
def _csrf_guard():
    if request.method != "POST":
        return None
    path = request.path
    if path.startswith(MACHINE_ENDPOINTS):
        return None
    if path.startswith("/api/") or path.startswith("/auth/"):
        if not _csrf_ok():
            return jsonify({"error": "Проверка безопасности не пройдена. Обнови страницу (Ctrl+R) и попробуй снова."}), 403
    return None

@app.get("/api/csrf")
def csrf_token():
    if not _current_user():
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"ok": True, "token": _csrf_token()})

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            total INTEGER NOT NULL DEFAULT 0,
            online INTEGER NOT NULL DEFAULT 0,
            clan INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS staff (
            user_id TEXT PRIMARY KEY,
            updated_at INTEGER NOT NULL,
            role_name TEXT
        );
        CREATE TABLE IF NOT EXISTS staff_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            updated_at INTEGER,
            complete INTEGER NOT NULL DEFAULT 0
        );        CREATE TABLE IF NOT EXISTS tickets (
            channel_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            number INTEGER NOT NULL,
            category TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            owner_name TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            updated_at INTEGER,
            complete INTEGER NOT NULL DEFAULT 0,
            guild_id TEXT
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            guild_id TEXT,
            action TEXT NOT NULL,
            actor_id TEXT,
            actor_name TEXT,
            target_id TEXT,
            target_name TEXT,
            details TEXT
        );
        CREATE TABLE IF NOT EXISTS invite_leaderboard (
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            invites INTEGER NOT NULL,
            active INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS stats_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            total INTEGER NOT NULL,
            online INTEGER NOT NULL,
            clan INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_stats_history_ts ON stats_history(ts);
        CREATE TABLE IF NOT EXISTS guild_stats (
            guild_id TEXT PRIMARY KEY,
            total INTEGER NOT NULL DEFAULT 0,
            online INTEGER NOT NULL DEFAULT 0,
            clan INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER,
            joins_7d TEXT
        );
        """)
        conn.execute("INSERT OR IGNORE INTO stats(id) VALUES(1)")
        conn.execute("INSERT OR IGNORE INTO staff_meta(id) VALUES(1)")
        conn.execute("INSERT OR IGNORE INTO tickets_meta(id) VALUES(1)")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(stats)").fetchall()}
        if "joins_7d" not in cols:
            conn.execute("ALTER TABLE stats ADD COLUMN joins_7d TEXT")
        hcols = {row[1] for row in conn.execute("PRAGMA table_info(stats_history)").fetchall()}
        if "guild_id" not in hcols:
            conn.execute("ALTER TABLE stats_history ADD COLUMN guild_id TEXT")
        conn.execute("UPDATE stats_history SET guild_id=? WHERE guild_id IS NULL", (GUILD_ID,))
        scols = {row[1] for row in conn.execute("PRAGMA table_info(staff)").fetchall()}
        if "role_name" not in scols:
            conn.execute("ALTER TABLE staff ADD COLUMN role_name TEXT")
        empty = conn.execute("SELECT COUNT(*) AS c FROM guild_stats").fetchone()["c"] == 0
        old = conn.execute("SELECT total, online, clan, updated_at, joins_7d FROM stats WHERE id=1").fetchone()
        if empty and old is not None and GUILD_ID:
            conn.execute(
                "INSERT OR IGNORE INTO guild_stats(guild_id, total, online, clan, updated_at, joins_7d) VALUES(?,?,?,?,?,?)",
                (GUILD_ID, old["total"], old["online"], old["clan"], old["updated_at"], old["joins_7d"]))

init_db()

def _id_str(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int) and v >= 0:
        v = str(v)
    if isinstance(v, str) and v.isascii() and v.isdigit() and 1 <= len(v) <= 25:
        return v
    return None

def _is_staff(user):
    return bool(user) and str(user.get("id") or "") in PANEL_ADMIN_IDS

def _get_staff_ids():
    with _db() as conn:
        rows = conn.execute("SELECT user_id FROM staff").fetchall()
        meta = conn.execute("SELECT updated_at, complete FROM staff_meta WHERE id=1").fetchone()
    return {r["user_id"] for r in rows}, (meta["updated_at"] if meta else None), bool(meta["complete"]) if meta else False

def _current_guild():
    gid = str(session.get("guild_id") or "").strip()
    if gid.isascii() and gid.isdigit() and 1 <= len(gid) <= 25:
        return gid
    return GUILD_ID

def _get_tickets(guild_id):
    with _db() as conn:
        rows = conn.execute(
            "SELECT channel_id, guild_id, number, category, owner_id, owner_name, created_at FROM tickets WHERE guild_id=? ORDER BY number DESC",
            (guild_id,)).fetchall()
        meta = conn.execute("SELECT updated_at, complete FROM tickets_meta WHERE id=1").fetchone()
    items = [dict(r) for r in rows]
    return items, (meta["updated_at"] if meta else None), bool(meta["complete"]) if meta else False

def _get_guild_stats(guild_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT total, online, clan, updated_at, joins_7d FROM guild_stats WHERE guild_id=?",
            (guild_id,)).fetchone()
    return row

@app.get("/")
def index():
    return send_from_directory(".", "index.html")

@app.get("/wipe.png")
def wipe_image():
    resp = send_from_directory(".", "wipe.png")
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

@app.get("/manifest.json")
def pwa_manifest():
    resp = send_from_directory(".", "manifest.json")
    resp.headers["Content-Type"] = "application/manifest+json"
    resp.headers["Cache-Control"] = "public, max-age=3600"
    return resp

@app.get("/icon-192.png")
@app.get("/icon-512.png")
def pwa_icon():
    name = "icon-192.png" if request.path.endswith("192.png") else "icon-512.png"
    resp = send_from_directory(".", name)
    resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/auth/discord")
def discord_login():
    if not CLIENT_ID or not REDIRECT_URI:
        return "Discord OAuth is not configured yet.", 503
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = {"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
              "response_type": "code", "scope": "identify guilds.members.read", "state": state}
    return redirect("https://discord.com/oauth2/authorize?" + urlencode(params))

def _check_panel_access(uid, access_token):
    if uid in PANEL_ADMIN_IDS:
        return True, None
    if not GUILD_ID or not TICKET_STAFF_ROLE_ID:
        return False, MSG_NOT_CONFIGURED
    if not access_token:
        return False, MSG_UNAVAILABLE
    try:
        r = requests.get(f"{API}/users/@me/guilds/{GUILD_ID}/member",
                         headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
    except requests.RequestException:
        return False, MSG_UNAVAILABLE
    if r.status_code == 404:
        return False, MSG_NOT_IN_GUILD
    if r.status_code != 200:
        return False, MSG_UNAVAILABLE
    try:
        roles = r.json().get("roles")
    except (ValueError, AttributeError):
        return False, MSG_UNAVAILABLE
    if not isinstance(roles, list):
        return False, MSG_UNAVAILABLE
    if TICKET_STAFF_ROLE_ID in {str(x) for x in roles}:
        return True, None
    return False, MSG_NO_ROLE

def _drop_session(msg):
    session.pop("user", None)
    session.pop("verified_at", None)
    session["auth_notice"] = msg

def _current_user():
    user = session.get("user")
    if not user:
        return None
    uid = str(user.get("id") or "")
    verified = session.get("verified_at")
    now = int(time.time())
    if not (uid.isascii() and uid.isdigit()):
        _drop_session(MSG_RECHECK)
        return None
    if isinstance(verified, bool) or not isinstance(verified, int):
        _drop_session(MSG_RECHECK)
        return None
    if uid in PANEL_ADMIN_IDS:
        return user
    staff_ids, updated, complete = _get_staff_ids()
    if complete and updated is not None and now - updated <= STAFF_FRESH_SECONDS:
        if uid not in staff_ids:
            _drop_session(MSG_REVOKED)
            return None
    elif now - verified > SESSION_MAX_UNVERIFIED:
        _drop_session(MSG_RECHECK)
        return None
    return user

@app.get("/auth/discord/callback")
def discord_callback():
    if request.args.get("error"):
        return "Discord login was cancelled.", 400
    code, state = request.args.get("code"), request.args.get("state")
    if not code or not state or state != session.pop("oauth_state", None):
        abort(400, "Invalid OAuth state.")
    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        return "Discord OAuth env missing.", 503
    token_response = requests.post(f"{API}/oauth2/token", data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT_URI
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    if not token_response.ok:
        return "Discord token exchange failed.", 502
    access_token = token_response.json().get("access_token")
    user_response = requests.get(f"{API}/users/@me",
        headers={"Authorization": f"Bearer {access_token}"}, timeout=15)
    if not user_response.ok:
        return "Could not retrieve Discord profile.", 502
    user = user_response.json()
    profile = {"id": user.get("id"), "username": user.get("username"),
        "global_name": user.get("global_name"),
        "avatar_url": f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png"
            if user.get("avatar") else None}
    allowed, reason = _check_panel_access(str(user.get("id") or ""), access_token)
    session.pop("user", None)
    session.pop("verified_at", None)
    if not allowed:
        session["auth_notice"] = reason
        return redirect("/")
    session["user"] = profile
    session["verified_at"] = int(time.time())
    return redirect("/")

@app.post("/api/update-stats")
def update_stats():
    provided = request.headers.get("X-Falcons-Secret", "")
    if not API_SECRET_KEY or not hmac.compare_digest(provided.encode(), API_SECRET_KEY.encode()):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid json"}), 400
    raw_gid = data.get("guild_id")
    guild_id = GUILD_ID if raw_gid is None else _id_str(raw_gid)
    if not guild_id:
        return jsonify({"ok": False, "error": "invalid guild_id"}), 400
    values = {}
    for key in ("total", "online", "clan"):
        v = data.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return jsonify({"ok": False, "error": f"invalid {key}"}), 400
        values[key] = v
    joins_7d = data.get("joins_7d")
    joins_json = None
    if isinstance(joins_7d, list) and len(joins_7d) == 7:
        clean = []
        ok = True
        for v in joins_7d:
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                ok = False
                break
            clean.append(v)
        if ok:
            joins_json = json.dumps(clean)
    now = int(time.time())
    with _db() as conn:
        conn.execute(
            "INSERT INTO guild_stats(guild_id, total, online, clan, updated_at, joins_7d) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(guild_id) DO UPDATE SET total=excluded.total, online=excluded.online, clan=excluded.clan, "
            "updated_at=excluded.updated_at, joins_7d=COALESCE(excluded.joins_7d, guild_stats.joins_7d)",
            (guild_id, values["total"], values["online"], values["clan"], now, joins_json))
        conn.execute(
            "INSERT INTO stats_history(ts, total, online, clan, guild_id) VALUES(?,?,?,?,?)",
            (now, values["total"], values["online"], values["clan"], guild_id))
    return jsonify({"ok": True})

@app.post("/api/update-staff")
def update_staff():
    provided = request.headers.get("X-Falcons-Secret", "")
    if not API_SECRET_KEY or not hmac.compare_digest(provided.encode(), API_SECRET_KEY.encode()):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid json"}), 400
    if data.get("complete") is not True:
        return jsonify({"ok": False, "error": "incomplete"}), 400
    guild_id = _id_str(data.get("guild_id"))
    if guild_id is None or (GUILD_ID and guild_id != GUILD_ID):
        return jsonify({"ok": False, "error": "invalid guild_id"}), 400
    raw = data.get("staff_ids")
    if not isinstance(raw, list) or len(raw) > MAX_STAFF:
        return jsonify({"ok": False, "error": "invalid staff_ids"}), 400
    ids = set()
    for v in raw:
        s = _id_str(v)
        if s is None:
            return jsonify({"ok": False, "error": "invalid staff id"}), 400
        ids.add(s)
    now = int(time.time())
    roles = {}
    raw_entries = data.get("entries")
    if isinstance(raw_entries, list):
        for v in raw_entries:
            if not isinstance(v, dict):
                return jsonify({"ok": False, "error": "invalid entries"}), 400
            sid = _id_str(v.get("id"))
            if sid is None:
                return jsonify({"ok": False, "error": "invalid staff id"}), 400
            ids.add(sid)
            role = v.get("role")
            roles[sid] = role.strip()[:80] if isinstance(role, str) and role.strip() else None
    with _db() as conn:
        conn.execute("DELETE FROM staff")
        for uid in ids:
            conn.execute("INSERT INTO staff(user_id, role_name, updated_at) VALUES(?,?,?)", (uid, roles.get(uid), now))
        conn.execute("UPDATE staff_meta SET updated_at=?, complete=1 WHERE id=1", (now,))
    return jsonify({"ok": True, "count": len(ids)})

@app.post("/api/update-tickets")
def update_tickets():
    provided = request.headers.get("X-Falcons-Secret", "")
    if not API_SECRET_KEY or not hmac.compare_digest(provided.encode(), API_SECRET_KEY.encode()):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid json"}), 400
    if data.get("complete") is not True:
        return jsonify({"ok": False, "error": "incomplete"}), 400
    guild_id = _id_str(data.get("guild_id"))
    if guild_id is None:
        return jsonify({"ok": False, "error": "invalid guild_id"}), 400
    raw = data.get("tickets")
    if not isinstance(raw, list):
        return jsonify({"ok": False, "error": "invalid tickets"}), 400
    if len(raw) > MAX_TICKETS:
        return jsonify({"ok": False, "error": "too many tickets"}), 400
    items = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            return jsonify({"ok": False, "error": f"invalid ticket {i}"}), 400
        number = t.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 0:
            return jsonify({"ok": False, "error": f"invalid number in ticket {i}"}), 400
        category = t.get("category")
        if category not in TICKET_CATEGORIES:
            return jsonify({"ok": False, "error": f"invalid category in ticket {i}"}), 400
        owner_id = _id_str(t.get("owner_id"))
        channel_id = _id_str(t.get("channel_id"))
        if owner_id is None or channel_id is None:
            return jsonify({"ok": False, "error": f"invalid id in ticket {i}"}), 400
        owner_name = t.get("owner_name")
        if not isinstance(owner_name, str) or len(owner_name) > 100:
            return jsonify({"ok": False, "error": f"invalid owner_name in ticket {i}"}), 400
        created_at = t.get("created_at")
        if isinstance(created_at, bool) or not isinstance(created_at, int) or created_at < 0:
            return jsonify({"ok": False, "error": f"invalid created_at in ticket {i}"}), 400
        items.append((channel_id, guild_id, number, category, owner_id, owner_name, created_at))
    now = int(time.time())
    with _db() as conn:
        conn.execute("DELETE FROM tickets WHERE guild_id=?", (guild_id,))
        for it in items:
            conn.execute(
                "INSERT INTO tickets(channel_id, guild_id, number, category, owner_id, owner_name, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (*it, now))
        if not GUILD_ID or guild_id == GUILD_ID:
            conn.execute("UPDATE tickets_meta SET updated_at=?, complete=1, guild_id=? WHERE id=1",
                         (now, guild_id))
    return jsonify({"ok": True, "count": len(items)})

@app.post("/api/update-invites")
def update_invites():
    provided = request.headers.get("X-Falcons-Secret", "")
    if not API_SECRET_KEY or not hmac.compare_digest(provided.encode(), API_SECRET_KEY.encode()):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid json"}), 400
    if data.get("complete") is not True:
        return jsonify({"ok": False, "error": "incomplete"}), 400
    guild_id = _id_str(data.get("guild_id"))
    if guild_id is None:
        return jsonify({"ok": False, "error": "invalid guild_id"}), 400
    raw = data.get("top")
    if not isinstance(raw, list) or len(raw) > 20:
        return jsonify({"ok": False, "error": "invalid top"}), 400
    items = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            return jsonify({"ok": False, "error": f"invalid entry {i}"}), 400
        user_id = _id_str(t.get("user_id"))
        if user_id is None:
            return jsonify({"ok": False, "error": f"invalid user_id in entry {i}"}), 400
        user_name = t.get("user_name")
        if not isinstance(user_name, str) or len(user_name) > 100:
            return jsonify({"ok": False, "error": f"invalid user_name in entry {i}"}), 400
        invites = t.get("invites")
        active = t.get("active")
        if isinstance(invites, bool) or not isinstance(invites, int) or invites < 0:
            return jsonify({"ok": False, "error": f"invalid invites in entry {i}"}), 400
        if isinstance(active, bool) or not isinstance(active, int) or active < 0:
            return jsonify({"ok": False, "error": f"invalid active in entry {i}"}), 400
        items.append((guild_id, user_id, user_name, invites, active))
    now = int(time.time())
    with _db() as conn:
        conn.execute("DELETE FROM invite_leaderboard WHERE guild_id=?", (guild_id,))
        for it in items:
            conn.execute(
                "INSERT INTO invite_leaderboard(guild_id, user_id, user_name, invites, active, updated_at) VALUES(?,?,?,?,?,?)",
                (*it, now))
    return jsonify({"ok": True, "count": len(items)})

@app.post("/api/log")
def api_log():
    provided = request.headers.get("X-Falcons-Secret", "")
    if not API_SECRET_KEY or not hmac.compare_digest(provided.encode(), API_SECRET_KEY.encode()):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid json"}), 400
    action = data.get("action")
    if not isinstance(action, str) or not action:
        return jsonify({"ok": False, "error": "invalid action"}), 400
    with _db() as conn:
        conn.execute(
            "INSERT INTO logs(ts, guild_id, action, actor_id, actor_name, target_id, target_name, details) VALUES(?,?,?,?,?,?,?,?)",
            (int(time.time()), str(data.get("guild_id") or ""), action,
             str(data.get("actor_id") or "") or None,
             str(data.get("actor_name") or "") or None,
             str(data.get("target_id") or "") or None,
             str(data.get("target_name") or "") or None,
             str(data.get("details") or "")[:2000]))
    return jsonify({"ok": True})

def _bot_post(path, payload):
    if not BOT_API_URL:
        return False, 503, {"ok": False, "error": "BOT_API_URL not configured"}
    url = BOT_API_URL + path
    try:
        r = requests.post(url, json=payload,
                          headers={"X-Falcons-Secret": BOT_API_SECRET},
                          timeout=15)
    except requests.RequestException as e:
        return False, 502, {"ok": False, "error": f"bot unreachable: {e}"}
    try:
        body = r.json()
    except ValueError:
        body = {"ok": False, "error": "invalid bot response", "raw": r.text[:300]}
    return (200 <= r.status_code < 300), r.status_code, body

def _bot_get(path):
    if not BOT_API_URL:
        return False, 503, {"ok": False, "error": "BOT_API_URL not configured"}
    url = BOT_API_URL + path
    try:
        r = requests.get(url, headers={"X-Falcons-Secret": BOT_API_SECRET}, timeout=15)
    except requests.RequestException as e:
        return False, 502, {"ok": False, "error": f"bot unreachable: {e}"}
    try:
        body = r.json()
    except ValueError:
        body = {"ok": False, "error": "invalid bot response", "raw": r.text[:300]}
    return (200 <= r.status_code < 300), r.status_code, body

@app.post("/api/ticket/accept")
def api_ticket_accept():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    channel_id = _id_str(data.get("channel_id"))
    if not channel_id:
        return jsonify({"ok": False, "error": "invalid channel_id"}), 400
    ok, status, body = _bot_post("/bot/ticket/accept", {
        "channel_id": channel_id, "actor_id": user.get("id")})
    return jsonify(body), status

@app.post("/api/ticket/reject")
def api_ticket_reject():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    channel_id = _id_str(data.get("channel_id"))
    reason = str(data.get("reason", "")).strip()
    if not channel_id:
        return jsonify({"ok": False, "error": "invalid channel_id"}), 400
    if not reason:
        return jsonify({"ok": False, "error": "reason required"}), 400
    ok, status, body = _bot_post("/bot/ticket/reject", {
        "channel_id": channel_id, "actor_id": user.get("id"), "reason": reason})
    return jsonify(body), status

@app.post("/api/ticket/close")
def api_ticket_close():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    channel_id = _id_str(data.get("channel_id"))
    if not channel_id:
        return jsonify({"ok": False, "error": "invalid channel_id"}), 400
    ok, status, body = _bot_post("/bot/ticket/close", {
        "channel_id": channel_id, "actor_id": user.get("id")})
    return jsonify(body), status

@app.get("/api/questions")
def api_questions_get():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    ok, status, body = _bot_get("/bot/questions")
    return jsonify(body), status

@app.post("/api/questions")
def api_questions_set():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    category = data.get("category")
    if category not in TICKET_CATEGORIES:
        return jsonify({"ok": False, "error": "invalid category"}), 400
    raw = data.get("questions")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 30:
        return jsonify({"ok": False, "error": "invalid questions"}), 400
    questions = []
    for q in raw:
        if not isinstance(q, str) or not q.strip() or len(q.strip()) > 500:
            return jsonify({"ok": False, "error": "invalid question"}), 400
        questions.append(q.strip())
    ok, status, body = _bot_post("/bot/questions", {"category": category, "questions": questions})
    return jsonify(body), status

@app.post("/api/reload-config")
def api_reload_config():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if not _is_staff(user):
        return jsonify({"ok": False, "error": "owner only"}), 403
    ok, status, body = _bot_post("/bot/reload_config", {})
    return jsonify(body), status

@app.get("/api/punishments")
def api_punishments():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    user_id = _id_str(request.args.get("user_id"))
    if not user_id:
        return jsonify({"ok": False, "error": "invalid user_id"}), 400
    try:
        limit = int(request.args.get("limit", "10"))
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))
    ok, status, body = _bot_get(f"/bot/punishments?user_id={user_id}&limit={limit}&guild_id={_current_guild()}")
    return jsonify(body), status

@app.post("/api/punish")
def api_punish():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("warn", "timeout", "kick", "ban"):
        return jsonify({"ok": False, "error": "invalid action"}), 400
    user_id = _id_str(data.get("user_id"))
    if not user_id:
        return jsonify({"ok": False, "error": "invalid user_id"}), 400
    reason = str(data.get("reason", "")).strip()
    if not reason or len(reason) > 500:
        return jsonify({"ok": False, "error": "invalid reason"}), 400
    payload = {"actor_id": user.get("id"), "action": action, "user_id": user_id, "reason": reason,
               "guild_id": _current_guild()}
    if action == "timeout":
        minutes = data.get("duration_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 40320:
            return jsonify({"ok": False, "error": "invalid duration"}), 400
        payload["duration_minutes"] = minutes
    ok, status, body = _bot_post("/bot/punish", payload)
    return jsonify(body), status

@app.get("/api/voice-channels")
def api_voice_channels():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    ok, status, body = _bot_get(f"/bot/voice-channels?guild_id={_current_guild()}")
    return jsonify(body), status

@app.post("/api/meeting")
def api_meeting():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    voice_id = _id_str(data.get("voice_channel_id"))
    if not voice_id:
        return jsonify({"ok": False, "error": "invalid voice_channel_id"}), 400
    ok, status, body = _bot_post("/bot/meeting", {"voice_channel_id": voice_id, "actor_id": user.get("id"),
                                                  "guild_id": _current_guild()})
    return jsonify(body), status

@app.get("/api/text-channels")
def api_text_channels():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    ok, status, body = _bot_get(f"/bot/text-channels?guild_id={_current_guild()}")
    return jsonify(body), status

@app.post("/api/wipe")
def api_wipe():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid json"}), 400
    channel_id = _id_str(data.get("channel_id"))
    title = str(data.get("title", "")).strip()
    description = str(data.get("description", "")).strip()
    server = str(data.get("server", "")).strip()
    date_text = str(data.get("date", "")).strip()
    time_text = str(data.get("time", "")).strip()
    if not channel_id or not title or len(title) > 256 or not description or len(description) > 3500 or len(server) > 100:
        return jsonify({"ok": False, "error": "invalid channel or announcement text"}), 400
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text) or not re.fullmatch(r"\d{2}:\d{2}", time_text):
        return jsonify({"ok": False, "error": "invalid date/time"}), 400
    if not isinstance(data.get("ask_for_reason", False), bool):
        return jsonify({"ok": False, "error": "invalid ask_for_reason"}), 400
    mention = str(data.get("mention", "main")).strip() or "main"
    image_url = str(data.get("image_url", "")).strip()
    if image_url and not image_url.lower().startswith(("http://", "https://")):
        return jsonify({"ok": False, "error": "invalid image_url"}), 400
    ok, status, body = _bot_post("/bot/wipe", {
        "guild_id": _current_guild(), "actor_id": user.get("id"), "channel_id": channel_id,
        "title": title, "description": description, "server": server,
        "date": date_text, "time": time_text, "ask_for_reason": data.get("ask_for_reason", False),
        "mention": mention, "image_url": image_url
    })
    return jsonify(body), status

@app.post("/api/invites/reward")
def api_invites_reward():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    user_id = _id_str(data.get("user_id"))
    if not user_id:
        return jsonify({"ok": False, "error": "invalid user_id"}), 400
    ok, status, body = _bot_post("/bot/invites/reward", {"user_id": user_id, "actor_id": user.get("id"),
                                                         "guild_id": _current_guild()})
    return jsonify(body), status

@app.get("/api/bot-status")
def api_bot_status():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    ok, status, body = _bot_get("/bot/status")
    if ok:
        return jsonify({"ok": True, "online": True, "bot": body})
    return jsonify({"ok": True, "online": False, "error": body.get("error", "unreachable")})

@app.get("/api/bot-settings")
def api_bot_settings_get():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if not _is_staff(user):
        return jsonify({"ok": False, "error": "owner only"}), 403
    ok, status, body = _bot_get("/bot/settings")
    return jsonify(body), status

@app.post("/api/bot-settings")
def api_bot_settings_set():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    if not _is_staff(user):
        return jsonify({"ok": False, "error": "owner only"}), 403
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid json"}), 400
    payload = {}
    for key in ("welcome_enabled", "goodbye_enabled"):
        if key in data:
            if not isinstance(data[key], bool):
                return jsonify({"ok": False, "error": f"invalid {key}"}), 400
            payload[key] = data[key]
    for key in ("welcome_text", "goodbye_text"):
        if key in data:
            if not isinstance(data[key], str) or len(data[key]) > 500:
                return jsonify({"ok": False, "error": f"invalid {key}"}), 400
            payload[key] = data[key].strip()
    if not payload:
        return jsonify({"ok": False, "error": "no settings"}), 400
    ok, status, body = _bot_post("/bot/settings", payload)
    return jsonify(body), status

@app.get("/api/analytics")
def api_analytics():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    since = int(time.time()) - 7 * 86400
    with _db() as conn:
        rows = conn.execute(
            "SELECT ts, total, online, clan FROM stats_history WHERE guild_id=? AND ts>=? ORDER BY ts ASC LIMIT 2000",
            (_current_guild(), since)).fetchall()
    return jsonify({"ok": True, "points": [dict(r) for r in rows]})

def _fetch_bot_guilds():
    ok, status, body = _bot_get("/bot/status")
    if not ok or not isinstance(body, dict):
        return None
    guilds = body.get("guilds")
    if not isinstance(guilds, list):
        return None
    out = []
    for g in guilds:
        if not isinstance(g, dict):
            continue
        gid = _id_str(g.get("id"))
        if gid is None:
            continue
        name = g.get("name")
        members = g.get("members")
        out.append({"id": gid,
                    "name": name if isinstance(name, str) and name else f"Сервер {gid}",
                    "members": members if isinstance(members, int) and members >= 0 else 0})
    return out

@app.get("/api/guilds")
def api_guilds():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    guilds = _fetch_bot_guilds()
    if guilds is None:
        guilds = [{"id": GUILD_ID, "name": "Основной сервер", "members": 0}] if GUILD_ID else []
    current = _current_guild()
    if not any(g["id"] == current for g in guilds):
        current = guilds[0]["id"] if guilds else None
    return jsonify({"ok": True, "guilds": guilds, "guild_id": current})

@app.post("/api/select-guild")
def api_select_guild():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid json"}), 400
    guild_id = _id_str(data.get("guild_id"))
    if guild_id is None:
        return jsonify({"ok": False, "error": "invalid guild_id"}), 400
    guilds = _fetch_bot_guilds()
    if guilds is None:
        if GUILD_ID and guild_id == GUILD_ID:
            session["guild_id"] = guild_id
            return jsonify({"ok": True, "guild_id": guild_id, "name": "Основной сервер"})
        return jsonify({"ok": False, "error": "bot unreachable"}), 503
    match = next((g for g in guilds if g["id"] == guild_id), None)
    if match is None:
        return jsonify({"ok": False, "error": "guild not available"}), 404
    session["guild_id"] = guild_id
    return jsonify({"ok": True, "guild_id": guild_id, "name": match["name"]})

@app.get("/api/me")
def me():
    user = _current_user()
    staff = bool(user)
    guild_id = _current_guild()
    s = _get_guild_stats(guild_id)
    joins_7d = None
    if s and s["joins_7d"]:
        try:
            joins_7d = json.loads(s["joins_7d"])
        except (ValueError, TypeError):
            joins_7d = None
    with _db() as conn:
        leaderboard_rows = conn.execute(
            "SELECT user_id, user_name, invites, active FROM invite_leaderboard WHERE guild_id=? ORDER BY active DESC, invites DESC LIMIT 20",
            (guild_id,)).fetchall()
    payload = {
        "authenticated": bool(user),
        "user": user,
        "guild_id": guild_id or None,
        "stats": {"total": s["total"], "online": s["online"], "clan": s["clan"]} if s else {"total": 0, "online": 0, "clan": 0},
        "stats_updated_at": s["updated_at"] if s else None,
        "joins_7d": joins_7d,
        "invite_leaderboard": [dict(r) for r in leaderboard_rows],
        "is_staff": staff,
        "server_time": int(time.time()),
    }
    if user:
        if str(user.get("id") or "") in PANEL_ADMIN_IDS:
            payload["panel_role"] = "owner"
        else:
            with _db() as conn:
                row = conn.execute(
                    "SELECT role_name FROM staff WHERE user_id=? AND role_name IS NOT NULL AND role_name!=''",
                    (str(user.get("id") or ""),)).fetchone()
            payload["panel_role"] = (row["role_name"] if row else None) or "staff"
    else:
        notice = session.pop("auth_notice", None)
        if notice:
            payload["notice"] = notice
    if staff:
        items, updated, complete = _get_tickets(guild_id)
        payload["tickets"] = {"items": items, "guild_id": guild_id or None,
                              "updated_at": updated, "complete": complete}
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.get("/api/logs")
def api_logs():
    user = _current_user()
    if not user:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    limit = min(int(request.args.get("limit", 100)), 500)
    with _db() as conn:
        rows = conn.execute(
            "SELECT ts, action, actor_id, actor_name, target_id, target_name, details FROM logs WHERE guild_id=? ORDER BY ts DESC LIMIT ?",
            (_current_guild(), limit)).fetchall()
    return jsonify({"ok": True, "logs": [dict(r) for r in rows]})

@app.post("/auth/logout")
def logout():
    session.pop("user", None)
    session.pop("verified_at", None)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
