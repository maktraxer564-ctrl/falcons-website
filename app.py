import os
import hmac
import time
import secrets
from urllib.parse import urlencode
import requests
from flask import Flask, redirect, request, session, jsonify, send_from_directory, abort

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI = os.environ.get("DISCORD_REDIRECT_URI", "")
API = "https://discord.com/api/v10"
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "")
# Live stats pushed by the Discord bot. Kept in memory: resets on restart until the bot sends again (max ~10 min).
LIVE_STATS = {"total": 0, "online": 0, "clan": 0}
LIVE_META = {"updated_at": None}

# --- Тикеты (этап 1а) ---
# Кто считается стаффом панели: Discord ID через запятую в переменной PANEL_ADMIN_IDS.
# Пусто = тикеты не видит никто. Проверка делается здесь, на сервере; скрытые кнопки защитой не считаются.
PANEL_ADMIN_IDS = {x.strip() for x in os.environ.get("PANEL_ADMIN_IDS", "").split(",") if x.strip().isdigit()}
# Лимит размера входящего запроса (тикеты): 300 КБ.
app.config["MAX_CONTENT_LENGTH"] = 300 * 1024
TICKET_CATEGORIES = {"clan", "partner", "question", "report", "other"}
MAX_TICKETS = 100

# --- Вход в панель только с ролью (этап 1.5) ---
# GUILD_ID и TICKET_STAFF_ROLE_ID: ID сервера и роли Ticket Staff (цифры, не секреты).
# Владельцы из PANEL_ADMIN_IDS входят всегда (защита от самоблокировки), остальные только с ролью.
GUILD_ID = os.environ.get("GUILD_ID", "").strip()
GUILD_ID = GUILD_ID if GUILD_ID.isdigit() else ""
TICKET_STAFF_ROLE_ID = os.environ.get("TICKET_STAFF_ROLE_ID", "").strip()
TICKET_STAFF_ROLE_ID = TICKET_STAFF_ROLE_ID if TICKET_STAFF_ROLE_ID.isdigit() else ""
SESSION_MAX_UNVERIFIED = 3600   # без свежего списка от бота сессия живёт не дольше часа без повторной проверки роли
STAFF_FRESH_SECONDS = 25 * 60   # список стаффа от бота считается свежим 25 минут
# Список ID участников с ролью Ticket Staff, присланный ботом (в памяти).
LIVE_STAFF = {"ids": set(), "updated_at": None}
MSG_NO_ROLE = ("Доступ запрещён. Для входа в панель FALCONS необходима роль TICKET STAFF на Discord-сервере. "
               "Обратись к администрации, чтобы получить доступ.")
MSG_NOT_IN_GUILD = "Доступ запрещён: ты не состоишь на Discord-сервере FALCONS."
MSG_UNAVAILABLE = "Проверка роли временно недоступна. Попробуй войти позже."
MSG_NOT_CONFIGURED = "Проверка ролей на сайте ещё не настроена. Обратись к администрации."
MSG_REVOKED = "Доступ закрыт: у тебя больше нет роли TICKET STAFF."
MSG_RECHECK = "Для безопасности нужно войти заново."
# Список открытых тикетов, присланный ботом. Хранится в памяти (после перезапуска пусто, пока бот не пришлёт снова).
LIVE_TICKETS = {"items": [], "guild_id": None, "updated_at": None}

def _is_staff(user):
    return bool(user) and str(user.get("id") or "") in PANEL_ADMIN_IDS

def _id_str(v):
    """Discord ID: целое число или строка из цифр (1-25 знаков) -> строка, иначе None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int) and v >= 0:
        v = str(v)
    if isinstance(v, str) and v.isascii() and v.isdigit() and 1 <= len(v) <= 25:
        return v
    return None

@app.get("/")
def index():
    return send_from_directory(".", "index.html")

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/auth/discord")
def discord_login():
    if not CLIENT_ID or not REDIRECT_URI:
        return "Discord OAuth is not configured yet. Set environment variables first.", 503
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    params = {"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
              "response_type": "code", "scope": "identify guilds.members.read", "state": state}
    return redirect("https://discord.com/oauth2/authorize?" + urlencode(params))

def _check_panel_access(uid, access_token):
    """(допущен, сообщение). Роль проверяется на сервере по ответу Discord, а не по данным браузера."""
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
    """Вошедший пользователь, у которого доступ всё ещё действует; иначе None."""
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
        _drop_session(MSG_RECHECK)      # сессия создана до проверки ролей
        return None
    if uid in PANEL_ADMIN_IDS:
        return user
    updated = LIVE_STAFF["updated_at"]
    if updated is not None and now - updated <= STAFF_FRESH_SECONDS:
        if uid not in LIVE_STAFF["ids"]:
            _drop_session(MSG_REVOKED)  # роль забрали после входа
            return None
    elif now - verified > SESSION_MAX_UNVERIFIED:
        _drop_session(MSG_RECHECK)      # бот давно не присылал список, проверяем роль заново
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
        return "Discord OAuth environment variables are missing.", 503
    token_response = requests.post(f"{API}/oauth2/token", data={
        "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": REDIRECT_URI
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=15)
    if not token_response.ok:
        return "Discord token exchange failed. Check OAuth2 credentials and redirect URI.", 502
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
    LIVE_STATS.update(values)
    LIVE_META["updated_at"] = int(time.time())
    return jsonify({"ok": True})

@app.post("/api/update-tickets")
def update_tickets():
    provided = request.headers.get("X-Falcons-Secret", "")
    if not API_SECRET_KEY or not hmac.compare_digest(provided.encode(), API_SECRET_KEY.encode()):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid json"}), 400
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
        items.append({"number": number, "category": category, "owner_id": owner_id,
                      "owner_name": owner_name, "channel_id": channel_id, "created_at": created_at})
    LIVE_TICKETS["items"] = items
    LIVE_TICKETS["guild_id"] = guild_id
    LIVE_TICKETS["updated_at"] = int(time.time())
    return jsonify({"ok": True, "count": len(items)})

@app.post("/api/update-staff")
def update_staff():
    provided = request.headers.get("X-Falcons-Secret", "")
    if not API_SECRET_KEY or not hmac.compare_digest(provided.encode(), API_SECRET_KEY.encode()):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid json"}), 400
    if data.get("complete") is not True:
        # Неполный список (бот не уверен в кеше участников) не принимаем: иначе можно по ошибке закрыть доступ всем.
        return jsonify({"ok": False, "error": "incomplete"}), 400
    guild_id = _id_str(data.get("guild_id"))
    if guild_id is None or (GUILD_ID and guild_id != GUILD_ID):
        return jsonify({"ok": False, "error": "invalid guild_id"}), 400
    raw = data.get("staff_ids")
    if not isinstance(raw, list) or len(raw) > 500:
        return jsonify({"ok": False, "error": "invalid staff_ids"}), 400
    ids = set()
    for v in raw:
        s = _id_str(v)
        if s is None:
            return jsonify({"ok": False, "error": "invalid staff id"}), 400
        ids.add(s)
    LIVE_STAFF["ids"] = ids
    LIVE_STAFF["updated_at"] = int(time.time())
    return jsonify({"ok": True, "count": len(ids)})

@app.get("/api/me")
def me():
    user = _current_user()
    staff = bool(user)   # попасть в панель могут только допущенные (Owner или роль Ticket Staff)
    payload = {"authenticated": bool(user), "user": user,
               "stats": dict(LIVE_STATS), "stats_updated_at": LIVE_META["updated_at"],
               "is_staff": staff, "server_time": int(time.time())}
    if user:
        payload["panel_role"] = "owner" if str(user.get("id") or "") in PANEL_ADMIN_IDS else "staff"
    else:
        notice = session.pop("auth_notice", None)
        if notice:
            payload["notice"] = notice
    if staff:
        payload["tickets"] = {"items": list(LIVE_TICKETS["items"]), "guild_id": LIVE_TICKETS["guild_id"],
                              "updated_at": LIVE_TICKETS["updated_at"]}
    resp = jsonify(payload)
    resp.headers["Cache-Control"] = "no-store"
    return resp

@app.post("/auth/logout")
def logout():
    session.pop("user", None)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
