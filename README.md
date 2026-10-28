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

## Wipe announcements

Staff can publish a wipe announcement from the dashboard's **Вайп** page or with the Discord slash command `/wipe`. Both options post an embed with date/time in Europe/Moscow and buttons for **Принял**, **Не смогу**, **Опоздаю**, and **Причины**. A user's latest response replaces their previous response. **Не смогу** and **Опоздаю** always open a modal asking for a reason; **Причины** shows staff the reasons from those buttons. When the announcement is published, members with the `main` role receive a DM with a link. Members who have not responded get a reminder after 24 hours. The `ask_for_reason` checkbox is kept for compatibility but no longer gates the reason modal.

The dashboard uses the existing bot HTTP API configuration (`BOT_API_URL` and `API_SECRET_KEY`). The bot needs permission to view and send messages and embed links in the chosen text channel. The bot database stores wipe announcements and responses so the buttons remain active after a restart.
