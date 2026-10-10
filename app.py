import os
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
              "response_type": "code", "scope": "identify", "state": state}
    return redirect("https://discord.com/oauth2/authorize?" + urlencode(params))

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
    session["user"] = {"id": user.get("id"), "username": user.get("username"),
        "global_name": user.get("global_name"),
        "avatar_url": f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png"
            if user.get("avatar") else None}
    return redirect("/")

@app.get("/api/me")
def me():
    user = session.get("user")
    return jsonify({"authenticated": bool(user), "user": user})

@app.post("/auth/logout")
def logout():
    session.pop("user", None)
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
