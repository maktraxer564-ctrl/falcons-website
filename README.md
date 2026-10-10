# FALCONS Dashboard v0.2

Uses the newly supplied dashboard HTML, with Flask and Discord OAuth2 login.

## Render
Build command: `pip install -r requirements.txt`
Start command: `gunicorn app:app`

Environment variables:
- `FLASK_SECRET_KEY`: long random secret
- `DISCORD_CLIENT_ID`: OAuth2 Client ID
- `DISCORD_CLIENT_SECRET`: OAuth2 Client Secret
- `DISCORD_REDIRECT_URI`: `https://YOUR-RENDER-DOMAIN/auth/discord/callback`

Add the exact same redirect URI in Discord Developer Portal → OAuth2 → Redirects.

Only Discord profile login is implemented. The dashboard's member counts, tickets, roles, and statistics remain demo content until the bot/guild integration is added.
