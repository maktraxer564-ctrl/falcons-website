import os
import hmac
import time
import secrets
import sqlite3
import json
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
BOT_API_URL = os.environ.get("BOT_API_URL", "").strip().rstrip("/")
BOT_API_SECRET = os.environ.get("API_SECRET_KEY", "").strip()

app.config["MAX_CONTENT_LENGTH"] = 300 * 1024
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
            updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS staff_meta (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            updated_at INTEGER,
            complete INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS tickets (
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
        """)
        conn.execute("INSERT OR IGNORE INTO stats(id) VALUES(1)")
        conn.execute("INSERT OR IGNORE INTO staff_meta(id) VALUES(1)")
        conn.execute("INSERT OR IGNORE INTO tickets_meta(id) VALUES(1)")

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

def _get_tickets():
    with _db() as conn:
        rows = conn.execute("SELECT channel_id, guild_id, number, category, owner_id, owner_name, created_at FROM tickets ORDER BY number DESC").fetchall()
        meta = conn.execute("SELECT updated_at, complete, guild_id FROM tickets_meta WHERE id=1").fetchone()
    items = [dict(r) for r in rows]
    return items, (meta["updated_at"] if meta else None), bool(meta["complete"]) if meta else False, (meta["guild_id"] if meta else None)

@app.get("/")
def index():
    return send_from_directory(".", "index.html")

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
    values = {}
    for key in ("total", "online", "clan"):
        v = data.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            return jsonify({"ok": False, "error": f"invalid {key}"}), 400
        values[key] = v
    with _db() as conn:
        conn.execute("UPDATE stats SET total=?, online=?, clan=?, updated_at=? WHERE id=1",
                     (values["total"], values["online"], values["clan"], int(time.time())))
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
    with _db() as conn:
        conn.execute("DELETE FROM staff")
        for uid in ids:
            conn.execute("INSERT INTO staff(user_id, updated_at) VALUES(?,?)", (uid, now))
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
        conn.execute("DELETE FROM tickets")
        for it in items:
            conn.execute(
                "INSERT INTO tickets(channel_id, guild_id, number, category, owner_id, owner_name, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (*it, now))
        conn.execute("UPDATE tickets_meta SET updated_at=?, complete=1, guild_id=? WHERE id=1",
                     (now, guild_id))
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

@app.get("/api/me")
def me():
    user = _current_user()
    staff = bool(user)
    with _db() as conn:
        s = conn.execute("SELECT total, online, clan, updated_at FROM stats WHERE id=1").fetchone()
    payload = {
        "authenticated": bool(user),
        "user": user,
        "stats": {"total": s["total"], "online": s["online"], "clan": s["clan"]} if s else {"total": 0, "online": 0, "clan": 0},
        "stats_updated_at": s["updated_at"] if s else None,
        "is_staff": staff,
        "server_time": int(time.time()),
    }
    if user:
        payload["panel_role"] = "owner" if str(user.get("id") or "") in PANEL_ADMIN_IDS else "staff"
    else:
        notice = session.pop("auth_notice", None)
        if notice:
            payload["notice"] = notice
    if staff:
        items, updated, complete, guild_id = _get_tickets()
        payload["tickets"] = {"items": items, "guild_id": guild_id,
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
            "SELECT ts, action, actor_id, actor_name, target_id, target_name, details FROM logs ORDER BY ts DESC LIMIT ?",
            (limit,)).fetchall()
    return jsonify({"ok": True, "logs": [dict(r) for r in rows]})

@app.post("/auth/logout")
def logout():
    session.pop("user", None)
    session.pop("verified_at", None)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
