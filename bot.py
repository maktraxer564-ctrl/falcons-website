import io
import os
import json
import re
import asyncio
import logging
import sqlite3
import time
import datetime
from pathlib import Path

import aiohttp
from aiohttp import web as aiohttp_web
import discord
from discord.ext import commands, tasks
from discord import app_commands
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH, override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("falcons")

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
CLAN_ROLE_ID = int(os.getenv("CLAN_ROLE_ID", "0") or 0)
OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
LEGACY_TICKET_STAFF_ROLE_ID = 1507860009630040245
_ticket_role_env = os.getenv("TICKET_STAFF_ROLE_ID", "").strip()
TICKET_STAFF_ROLE_ID = int(_ticket_role_env) if _ticket_role_env.isdigit() else LEGACY_TICKET_STAFF_ROLE_ID
_dep_env = os.getenv("DEP_LEADER_ROLE_ID", "").strip()
DEP_LEADER_ROLE_ID = int(_dep_env) if _dep_env.isdigit() else 0
_log_env = os.getenv("STAFF_LOG_CHANNEL_ID", "").strip()
STAFF_LOG_CHANNEL_ID = int(_log_env) if _log_env.isdigit() else 0
VOICE_CATEGORY_NAME = os.getenv("VOICE_CATEGORY_NAME", "").strip()
CATEGORY_NAME = os.getenv("STATS_CATEGORY_NAME", "▪・ꜱᴇʀᴠᴇʀ ꜱᴛᴀᴛꜱ").strip()
TICKET_CATEGORY_NAME = os.getenv("TICKET_CATEGORY_NAME", "FLCN TICKETS").strip()
UPDATE_SECONDS = max(60, int(os.getenv("UPDATE_SECONDS", "60") or 60))
VOICE_UPDATE_SECONDS = max(60, int(os.getenv("VOICE_UPDATE_SECONDS", "420") or 420))
WEBSITE_URL = os.getenv("WEBSITE_URL", "https://falcons-panel.onrender.com").strip().rstrip("/")
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "").strip()
BOT_HTTP_PORT = int(os.getenv("BOT_HTTP_PORT", os.getenv("SERVER_PORT", "8080")) or 8080)
BOT_HTTP_HOST = os.getenv("BOT_HTTP_HOST", "0.0.0.0").strip()
NEWCOMER_ROLE_ID = int(os.getenv("NEWCOMER_ROLE_ID", "0") or 0)
MAIN_ROLE_ID = int(os.getenv("MAIN_ROLE_ID", "0") or 0)
REWARD_ROLE_10 = int(os.getenv("REWARD_ROLE_10", "0") or 0)
REWARD_ROLE_25 = int(os.getenv("REWARD_ROLE_25", "0") or 0)
REWARD_ROLE_50 = int(os.getenv("REWARD_ROLE_50", "0") or 0)
REWARD_ROLE_100 = int(os.getenv("REWARD_ROLE_100", "0") or 0)
INVITE_REWARDS = ((10, REWARD_ROLE_10), (25, REWARD_ROLE_25), (50, REWARD_ROLE_50), (100, REWARD_ROLE_100))
INVITE_FAKE_SECONDS = 600

TRANSCRIPT_CHANNEL_ID = int(os.getenv("TRANSCRIPT_CHANNEL_ID", "0") or 0)
MEETING_CHANNEL_ID = int(os.getenv("MEETING_CHANNEL_ID", "1560429785119723590") or 1560429785119723590)
TICKET_REMINDER_MINUTES = int(os.getenv("TICKET_REMINDER_MINUTES", "30") or 30)
RUST_MIN_HOURS = int(os.getenv("RUST_MIN_HOURS", "3000") or 3000)
WARN_TIMEOUT_COUNT = int(os.getenv("WARN_TIMEOUT_COUNT", "3") or 3)
WARN_TIMEOUT_MINUTES = int(os.getenv("WARN_TIMEOUT_MINUTES", "60") or 60)
WARN_KICK_COUNT = int(os.getenv("WARN_KICK_COUNT", "5") or 5)
WELCOME_CHANNEL_ID = int(os.getenv("WELCOME_CHANNEL_ID", "0") or 0)
WELCOME_TEXT = os.getenv("WELCOME_TEXT", "Добро пожаловать на сервер FALCONS, {mention}!").strip()
GOODBYE_CHANNEL_ID = int(os.getenv("GOODBYE_CHANNEL_ID", "0") or 0)
GOODBYE_ENABLED = os.getenv("GOODBYE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")
GOODBYE_TEXT = os.getenv("GOODBYE_TEXT", "{name} покинул сервер FALCONS.").strip()
SELF_ROLES = os.getenv("SELF_ROLES", "").strip()
AUTO_ANNOUNCE = os.getenv("AUTO_ANNOUNCE", "").strip()

SPAM_WINDOW_SECONDS = 3.0
SPAM_MESSAGE_LIMIT = 5
SPAM_TIMEOUT_MINUTES = 10

DB_PATH = str(Path(__file__).resolve().parent / "falcons.db")

if "TICKET_STAFF_ROLE_ID" not in os.environ:
    log.warning("TICKET_STAFF_ROLE_ID не в .env, используется fallback %s", LEGACY_TICKET_STAFF_ROLE_ID)

NAMES = {"members": "👥・ᴍᴇᴍʙᴇʀs", "online": "🟢・ᴏɴʟɪɴᴇ", "clan": "🛡️・ᴄʟᴀɴ"}

CATEGORY_QUESTIONS = {
    "clan": [
        "Возраст — сколько тебе лет?",
        "Часы в Rust — сколько часов наиграно?",
        "Steam — ссылка на профиль.",
        "BattleMetrics — ссылка на профиль.",
        "О себе — на какую роль хочешь в клане? Сколько времени в день играешь? Играешь ли каждый день?",
    ],
    "partner": ["Реклама с @everyone или без?", "Твой текст для рекламы", "На какой срок размещается реклама?"],
    "question": ["Тема вопроса", "Подробности вопроса"],
    "report": ["Ник и ID игрока, на которого жалуешься", "Что произошло? Укажи дату и подробности", "Ссылка или описание доказательств"],
    "other": ["Тема обращения", "Подробно опиши, чем мы можем помочь"],
}
CATEGORY_LABELS = {"clan": "Набор в клан", "partner": "Партнёрство", "question": "Вопрос по клану", "report": "Жалоба на игрока", "other": "Другое обращение"}

def _db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tickets (
            channel_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            number INTEGER NOT NULL,
            category TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            owner_name TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            decided_by INTEGER,
            decided_at INTEGER,
            decision TEXT,
            reason TEXT,
            claimed_by TEXT,
            reminded INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_owner ON tickets(guild_id, owner_id);
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
        CREATE TABLE IF NOT EXISTS counters (
            guild_id INTEGER PRIMARY KEY,
            ticket_number INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS ticket_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            position INTEGER NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS joins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            ts INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_joins_ts ON joins(guild_id, ts);
        CREATE TABLE IF NOT EXISTS punishments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            mod_id TEXT,
            mod_name TEXT,
            action TEXT NOT NULL,
            reason TEXT,
            duration INTEGER,
            ts INTEGER NOT NULL,
            expires_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS invites_cache (
            guild_id TEXT NOT NULL,
            code TEXT NOT NULL,
            uses INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, code)
        );
        CREATE TABLE IF NOT EXISTS invites (
            guild_id TEXT NOT NULL,
            inviter_id TEXT NOT NULL,
            invited_id TEXT NOT NULL,
            code TEXT NOT NULL,
            joined_at INTEGER NOT NULL,
            left_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS guild_config (
            guild_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            PRIMARY KEY (guild_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_invites_invited ON invites(guild_id, invited_id);
        """)
        ticket_cols = {row[1] for row in conn.execute("PRAGMA table_info(tickets)").fetchall()}
        if "claimed_by" not in ticket_cols:
            conn.execute("ALTER TABLE tickets ADD COLUMN claimed_by TEXT")
        if "reminded" not in ticket_cols:
            conn.execute("ALTER TABLE tickets ADD COLUMN reminded INTEGER NOT NULL DEFAULT 0")

def next_ticket_number(guild_id):
    with _db() as conn:
        row = conn.execute("SELECT ticket_number FROM counters WHERE guild_id=?", (guild_id,)).fetchone()
        current = row["ticket_number"] if row else 0
        new = current + 1
        if row:
            conn.execute("UPDATE counters SET ticket_number=? WHERE guild_id=?", (new, guild_id))
        else:
            conn.execute("INSERT INTO counters(guild_id, ticket_number) VALUES(?,?)", (guild_id, new))
        return new

def log_action(guild_id, action, actor=None, target=None, details=""):
    with _db() as conn:
        conn.execute(
            "INSERT INTO logs(ts, guild_id, action, actor_id, actor_name, target_id, target_name, details) VALUES(?,?,?,?,?,?,?,?)",
            (int(time.time()), str(guild_id), action,
             str(actor.id) if actor else None, str(actor) if actor else None,
             str(target.id) if target else None, str(target) if target else None, details))

def record_ticket_open(guild_id, number, category, owner, channel_id):
    with _db() as conn:
        conn.execute(
            "INSERT INTO tickets(channel_id, guild_id, number, category, owner_id, owner_name, created_at, updated_at, status) VALUES(?,?,?,?,?,?,?,?, 'open')",
            (str(channel_id), str(guild_id), number, category, str(owner.id), str(owner), int(time.time()), int(time.time())))

def record_ticket_decision(channel_id, decision, actor, reason=None):
    with _db() as conn:
        conn.execute(
            "UPDATE tickets SET status='closed', decided_by=?, decided_at=?, decision=?, reason=? WHERE channel_id=?",
            (actor.id if actor else None, int(time.time()), decision, reason, str(channel_id)))

def get_ticket_by_channel(channel_id):
    with _db() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE channel_id=?", (str(channel_id),)).fetchone()
        return dict(row) if row else None

def claim_ticket(channel_id, actor):
    with _db() as conn:
        conn.execute("UPDATE tickets SET claimed_by=? WHERE channel_id=?",
                     (str(actor.id), str(channel_id)))

def get_stale_tickets(guild_id, older_than_seconds):
    cutoff = int(time.time()) - int(older_than_seconds)
    with _db() as conn:
        rows = conn.execute(
            "SELECT channel_id, number, category, owner_id, owner_name, created_at, claimed_by FROM tickets"
            " WHERE guild_id=? AND status='open' AND reminded=0 AND created_at<=?",
            (str(guild_id), cutoff)).fetchall()
    return [dict(r) for r in rows]

def mark_reminded(channel_id):
    with _db() as conn:
        conn.execute("UPDATE tickets SET reminded=1 WHERE channel_id=?", (str(channel_id),))

def count_warns(guild_id, user_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM punishments WHERE guild_id=? AND user_id=? AND action='warn'",
            (str(guild_id), str(user_id))).fetchone()
    return row["c"] if row else 0

def get_setting(key, default=None):
    with _db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (str(key),)).fetchone()
    return row["value"] if row else default

def set_setting(key, value):
    with _db() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(key), str(value)))

def setting_bool(key, default):
    raw = get_setting(key)
    if raw is None:
        return default
    return raw == "1"

def get_guild_config(guild_id, key):
    with _db() as conn:
        row = conn.execute("SELECT value FROM guild_config WHERE guild_id=? AND key=?",
                           (str(guild_id), str(key))).fetchone()
    return row["value"] if row else None

def set_guild_config(guild_id, key, value):
    with _db() as conn:
        if value:
            conn.execute(
                "INSERT INTO guild_config(guild_id, key, value) VALUES(?,?,?)"
                " ON CONFLICT(guild_id, key) DO UPDATE SET value=excluded.value",
                (str(guild_id), str(key), str(value)))
        else:
            conn.execute("DELETE FROM guild_config WHERE guild_id=? AND key=?",
                         (str(guild_id), str(key)))

def _config_id(guild, key, env_value):
    value = get_guild_config(guild.id, key)
    if value is not None and value.isdigit():
        return int(value)
    if not GUILD_ID or guild.id == GUILD_ID:
        return env_value
    return 0

def _config_mention(guild, key, env_value, getter):
    value = _config_id(guild, key, env_value)
    obj = getter(value) if value else None
    return obj.mention if obj is not None else None

def _current_guild_config(guild):
    staff = get_staff_role(guild)
    clan_id = _config_id(guild, "clan_role", CLAN_ROLE_ID)
    clan = guild.get_role(clan_id) if clan_id else None
    if clan is None:
        clan = discord.utils.find(lambda r: r.name.casefold() == "clan", guild.roles)
    return {
        "staff_role": staff.mention if staff is not None else None,
        "clan_role": clan.mention if clan is not None else None,
        "welcome_channel": _config_mention(guild, "welcome_channel", WELCOME_CHANNEL_ID, guild.get_channel),
        "goodbye_channel": _config_mention(guild, "goodbye_channel", GOODBYE_CHANNEL_ID, guild.get_channel),
        "transcript_channel": _config_mention(guild, "transcript_channel", TRANSCRIPT_CHANNEL_ID, guild.get_channel),
        "log_channel": _config_mention(guild, "log_channel", STAFF_LOG_CHANNEL_ID, guild.get_channel),
        "meeting_channel": _config_mention(guild, "meeting_channel", MEETING_CHANNEL_ID, guild.get_channel),
        "newcomer_role": _config_mention(guild, "newcomer_role", NEWCOMER_ROLE_ID, guild.get_role),
        "main_role": _config_mention(guild, "main_role", MAIN_ROLE_ID, guild.get_role),
    }

def get_previous_tickets(guild_id, owner_id, limit=5):
    with _db() as conn:
        rows = conn.execute(
            "SELECT number, category, created_at, status, decision, reason FROM tickets WHERE guild_id=? AND owner_id=? ORDER BY created_at DESC LIMIT ?",
            (str(guild_id), str(owner_id), limit)).fetchall()
        return [dict(r) for r in rows]

def load_questions(category):
    with _db() as conn:
        rows = conn.execute("SELECT text FROM ticket_questions WHERE category=? ORDER BY position ASC", (category,)).fetchall()
    if rows:
        return [r["text"] for r in rows]
    return CATEGORY_QUESTIONS.get(category, CATEGORY_QUESTIONS["other"]).copy()

def save_questions(category, questions):
    with _db() as conn:
        conn.execute("DELETE FROM ticket_questions WHERE category=?", (category,))
        for i, q in enumerate(questions):
            conn.execute("INSERT INTO ticket_questions(category, position, text) VALUES(?,?,?)", (category, i, q))

def get_joins_last_7_days(guild_id):
    """Сколько человек зашло на сервер в каждый из последних 7 дней.
    Возвращает [6-дней-назад, ..., сегодня]."""
    now = datetime.datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    result = []
    with _db() as conn:
        for day_offset in range(6, -1, -1):
            start = int((today_start - datetime.timedelta(days=day_offset)).timestamp())
            end = start + 86400
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM joins WHERE guild_id=? AND ts>=? AND ts<?",
                (str(guild_id), start, end)).fetchone()
            result.append(row["c"] if row else 0)
    return result

init_db()
class FalconsBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.presences = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            try:
                self.tree.copy_global_to(guild=guild_obj)
                await self.tree.sync(guild=guild_obj)
                log.info("commands synced for guild %s", GUILD_ID)
            except Exception:
                log.exception("sync failed for guild %s", GUILD_ID)
        self.add_view(TicketActionsView())
        self.add_view(CloseTicketView())
        self.add_view(TicketPanelView())
        self.loop.create_task(start_bot_http())

bot = FalconsBot()

def has_staff_perms(member):
    if member is None:
        return False
    if OWNER_ID and member.id == OWNER_ID:
        return True
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    staff_role = get_staff_role(member.guild) if member.guild else None
    if staff_role is not None and staff_role in member.roles:
        return True
    for r in member.roles:
        if TICKET_STAFF_ROLE_ID and r.id == TICKET_STAFF_ROLE_ID:
            return True
        if DEP_LEADER_ROLE_ID and r.id == DEP_LEADER_ROLE_ID:
            return True
        name = r.name.casefold()
        if name == "ticket staff":
            return True
        if "dep" in name and "lead" in name:
            return True
    return False

def get_staff_role(guild):
    role_id = _config_id(guild, "staff_role", TICKET_STAFF_ROLE_ID)
    role = guild.get_role(role_id) if role_id else None
    if role is None:
        role = discord.utils.find(lambda r: "ticket" in r.name.casefold() and "staff" in r.name.casefold(), guild.roles)
    return role

def is_staff_interaction(interaction):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    return has_staff_perms(interaction.user)

async def send_staff_log(guild, embed):
    channel_id = _config_id(guild, "log_channel", STAFF_LOG_CHANNEL_ID)
    if not channel_id:
        return
    ch = guild.get_channel(channel_id)
    if ch is None:
        return
    try:
        await ch.send(embed=embed)
    except Exception:
        log.exception("Не удалось отправить лог")

def make_log_embed(title, color, fields):
    e = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())
    for name, value in fields:
        if value:
            e.add_field(name=name, value=str(value)[:1024], inline=False)
    return e

async def get_category(guild):
    wanted = CATEGORY_NAME.casefold()
    category = next((c for c in guild.categories if c.name.casefold() == wanted), None)
    if category:
        return category
    return await guild.create_category(CATEGORY_NAME, reason="FLCN stats")

async def update_counter(guild, category, key, count):
    prefix = NAMES[key]
    channel = discord.utils.find(
        lambda c: c.name == prefix or c.name.startswith(prefix + " :") or c.name.startswith(prefix + ":"),
        guild.voice_channels)
    target = f"{prefix} : {count}"
    if channel is None:
        overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True)}
        await guild.create_voice_channel(target, category=category, overwrites=overwrites, reason="FLCN stats")
    elif channel.name != target:
        await channel.edit(name=target, reason="FLCN stats refresh")
    if channel and channel.category_id != category.id:
        await channel.edit(category=category, reason="Organize FLCN stats")

refresh_lock = asyncio.Lock()
_last_voice_update = {}

async def push_stats(guild, total, online, clan):
    if not API_SECRET_KEY:
        return
    try:
        joins_7d = get_joins_last_7_days(guild.id)
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(f"{WEBSITE_URL}/api/update-stats",
                              json={"guild_id": str(guild.id), "total": total, "online": online, "clan": clan,
                                    "joins_7d": joins_7d},
                              headers={"X-Falcons-Secret": API_SECRET_KEY}) as r:
                if r.status != 200:
                    log.error("update-stats HTTP %s", r.status)
    except Exception:
        log.exception("push_stats failed")

async def push_staff(guild):
    if not API_SECRET_KEY or not TICKET_STAFF_ROLE_ID:
        return
    if GUILD_ID and guild.id != GUILD_ID:
        return
    try:
        role = guild.get_role(TICKET_STAFF_ROLE_ID)
        if role is None:
            return
        ids = [str(m.id) for m in role.members if not m.bot]
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(f"{WEBSITE_URL}/api/update-staff",
                              json={"guild_id": str(guild.id), "staff_ids": ids, "complete": True},
                              headers={"X-Falcons-Secret": API_SECRET_KEY}) as r:
                if r.status != 200:
                    log.error("update-staff HTTP %s", r.status)
    except Exception:
        log.exception("push_staff failed")

async def collect_tickets(guild):
    with _db() as conn:
        rows = conn.execute(
            "SELECT number, category, owner_id, owner_name, channel_id, created_at FROM tickets WHERE guild_id=? AND status='open'",
            (str(guild.id),)).fetchall()
    out = []
    for r in rows:
        ch = guild.get_channel(int(r["channel_id"]))
        if ch is None:
            with _db() as conn:
                conn.execute("UPDATE tickets SET status='closed', decision='orphan', decided_at=? WHERE channel_id=?",
                             (int(time.time()), r["channel_id"]))
            continue
        out.append({"number": r["number"], "category": r["category"],
                    "owner_id": str(r["owner_id"]), "owner_name": r["owner_name"],
                    "channel_id": str(r["channel_id"]), "created_at": r["created_at"]})
    return out

async def push_tickets(guild):
    if not API_SECRET_KEY:
        return
    try:
        items = await collect_tickets(guild)
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(f"{WEBSITE_URL}/api/update-tickets",
                              json={"guild_id": str(guild.id), "tickets": items, "complete": True},
                              headers={"X-Falcons-Secret": API_SECRET_KEY}) as r:
                if r.status != 200:
                    log.error("update-tickets HTTP %s", r.status)
                else:
                    log.info("Website tickets updated: count=%s", len(items))
    except Exception:
        log.exception("push_tickets failed")

async def refresh(guild):
    async with refresh_lock:
        try:
            await guild.chunk(cache=True)
        except discord.HTTPException as e:
            if e.status == 429:
                log.warning("chunk rate limited, skipping refresh for guild %s", guild.id)
                return
            log.exception("chunk failed")
        except Exception:
            log.exception("chunk failed")
        members = list(guild.members)
        total = guild.member_count if guild.member_count is not None else len(members)
        online = sum(1 for m in members if m.status not in (discord.Status.offline, discord.Status.invisible))
        clan_role_id = _config_id(guild, "clan_role", CLAN_ROLE_ID)
        clan_role = guild.get_role(clan_role_id) if clan_role_id else None
        if clan_role is None:
            clan_role = discord.utils.find(lambda r: r.name.casefold() == "clan", guild.roles)
        clan = sum(1 for m in members if clan_role and clan_role in m.roles)
        await push_stats(guild, total, online, clan)
        await push_invites(guild)
        await push_staff(guild)
        await push_tickets(guild)
        if not GUILD_ID or guild.id == GUILD_ID:
            if time.time() - _last_voice_update.get(guild.id, 0) < VOICE_UPDATE_SECONDS:
                return
            try:
                category = await get_category(guild)
                for key, count in (("members", total), ("online", online), ("clan", clan)):
                    await update_counter(guild, category, key, count)
                    await asyncio.sleep(1.2)
                _last_voice_update[guild.id] = time.time()
            except discord.HTTPException as e:
                if e.status == 429:
                    log.warning("counters rate limited, skipping voice update for guild %s", guild.id)
                else:
                    log.exception("counters failed")
            except Exception:
                log.exception("counters failed")

@tasks.loop(seconds=UPDATE_SECONDS)
async def updater():
    for guild in bot.guilds:
        try:
            await refresh(guild)
        except Exception:
            log.exception("auto refresh failed")

@updater.before_loop
async def before_updater():
    await bot.wait_until_ready()

async def remind_stale_tickets(guild):
    if TICKET_REMINDER_MINUTES <= 0:
        return
    for row in get_stale_tickets(guild.id, TICKET_REMINDER_MINUTES * 60):
        channel = guild.get_channel(int(row["channel_id"]))
        if channel is None:
            mark_reminded(row["channel_id"])
            continue
        minutes = max(1, (int(time.time()) - int(row["created_at"])) // 60)
        embed = discord.Embed(title="⏰ Тикет ждёт ответа", color=discord.Color.orange(),
                              description=f"Тикет #{row['number']} открыт {minutes} мин без ответа стаффа.")
        if row.get("claimed_by"):
            embed.add_field(name="Взял в работу", value=f"<@{row['claimed_by']}>", inline=False)
        staff_role = get_staff_role(guild)
        try:
            await channel.send(content=staff_role.mention if staff_role else None, embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Не удалось отправить напоминание о тикете")
        mark_reminded(row["channel_id"])

@tasks.loop(minutes=5)
async def ticket_reminder():
    for guild in bot.guilds:
        try:
            await remind_stale_tickets(guild)
        except Exception:
            log.exception("ticket_reminder failed")

@ticket_reminder.before_loop
async def before_ticket_reminder():
    await bot.wait_until_ready()

_announce_last_sent = {}

def parse_auto_announces():
    entries = []
    for part in AUTO_ANNOUNCE.split(";"):
        part = part.strip()
        if not part:
            continue
        bits = part.split("@", 2)
        if len(bits) != 3:
            continue
        channel_id, hhmm, text = bits[0].strip(), bits[1].strip(), bits[2].strip()
        if not (channel_id.isascii() and channel_id.isdigit()) or not text:
            continue
        if not re.fullmatch(r"\d{1,2}:\d{2}", hhmm):
            continue
        hh, mm = (int(x) for x in hhmm.split(":"))
        if hh > 23 or mm > 59:
            continue
        entries.append((int(channel_id), hh * 60 + mm, text))
    return entries

@tasks.loop(seconds=45)
async def auto_announce_loop():
    entries = parse_auto_announces()
    if not entries:
        return
    now = datetime.datetime.now()
    today_key = now.strftime("%Y-%m-%d")
    current = now.hour * 60 + now.minute
    for channel_id, minutes, text in entries:
        key = (channel_id, minutes, text)
        if current < minutes or _announce_last_sent.get(key) == today_key:
            continue
        channel = bot.get_channel(channel_id)
        if channel is None:
            guild = bot.get_guild(GUILD_ID) if GUILD_ID else (bot.guilds[0] if bot.guilds else None)
            if guild is not None:
                channel = guild.get_channel(channel_id)
        if channel is None:
            log.warning("auto_announce: канал %s не найден", channel_id)
            continue
        try:
            await channel.send(text)
            _announce_last_sent[key] = today_key
        except (discord.Forbidden, discord.HTTPException):
            log.exception("auto_announce: не удалось отправить")

@auto_announce_loop.before_loop
async def before_auto_announce():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    log.info("Connected as %s (%s)", bot.user, bot.user.id)
    log.info("on_ready: найдено %s гилдов: %s", len(bot.guilds), [g.id for g in bot.guilds])
    if not updater.is_running():
        updater.start()
    if not ticket_reminder.is_running():
        ticket_reminder.start()
    if not auto_announce_loop.is_running():
        auto_announce_loop.start()
    for guild in bot.guilds:
        try:
            await refresh(guild)
        except Exception:
            log.exception("initial refresh failed")
    for guild in bot.guilds:
        try:
            await collect_invites(guild)
        except Exception:
            log.exception("Не удалось собрать кэш инвайтов")
    for guild in bot.guilds:
        if GUILD_ID and guild.id == GUILD_ID:
            continue
        try:
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            log.info("commands synced for guild %s", guild.id)
        except Exception:
            log.exception("sync команд не удался для сервера %s", guild.id)

@bot.event
async def on_guild_join(guild):
    log.info("Бот добавлен на сервер %s (%s)", guild.name, guild.id)
    try:
        await bot.tree.sync(guild=discord.Object(id=guild.id))
    except Exception:
        log.exception("sync команд не удался для сервера %s", guild.id)
    try:
        await refresh(guild)
    except Exception:
        log.exception("initial refresh failed")
    try:
        await collect_invites(guild)
    except Exception:
        log.exception("Не удалось собрать кэш инвайтов")

@bot.event
async def on_member_join(member):
    try:
        with _db() as conn:
            conn.execute("INSERT INTO joins(guild_id, user_id, ts) VALUES(?,?,?)",
                         (str(member.guild.id), str(member.id), int(time.time())))
    except Exception:
        log.exception("Не удалось записать join")
    newcomer_id = _config_id(member.guild, "newcomer_role", NEWCOMER_ROLE_ID)
    if newcomer_id:
        role = member.guild.get_role(newcomer_id)
        if role is None:
            log.warning("newcomer_role=%s не найден на сервере %s", newcomer_id, member.guild.id)
        else:
            try:
                await member.add_roles(role, reason="Автороль новичка")
            except (discord.Forbidden, discord.HTTPException):
                log.exception("Не удалось выдать автороль новичку")
    try:
        await handle_invite_join(member)
    except Exception:
        log.exception("Обработка инвайта не удалась")
    try:
        await send_welcome(member)
    except Exception:
        log.exception("Приветствие не отправлено")
    try:
        await refresh(member.guild)
    except Exception:
        pass

@bot.event
async def on_member_remove(member):
    try:
        await refresh(member.guild)
    except Exception:
        pass
    try:
        mark_invite_left(member.guild.id, member.id)
    except Exception:
        log.exception("Не удалось отметить выход по инвайту")
    try:
        await send_goodbye(member)
    except Exception:
        log.exception("Прощание не отправлено")
            


def safe_channel_name(name):
    cleaned = re.sub(r"[^a-z0-9-]", "-", name.lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return (cleaned or "player")[:18]

async def get_ticket_category(guild):
    cat = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
    staff_role = get_staff_role(guild)
    bot_member = guild.me
    required = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    if staff_role:
        required[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True, manage_messages=True)
    if bot_member:
        required[bot_member] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True, manage_channels=True, manage_messages=True)
    if cat is None:
        return await guild.create_category(TICKET_CATEGORY_NAME, overwrites=required, reason="FLCN tickets")
    current = dict(cat.overwrites)
    changed = False
    for target, overwrite in required.items():
        if current.get(target) != overwrite:
            current[target] = overwrite
            changed = True
    if changed:
        await cat.edit(overwrites=current, reason="Обновление прав категории")
    return cat

def build_history_text(guild, owner_id):
    prev = get_previous_tickets(guild.id, owner_id, limit=5)
    if not prev:
        return "Предыдущих заявок не найдено"
    lines = []
    for p in prev:
        dt = time.strftime("%d.%m.%Y", time.localtime(p["created_at"]))
        dec = p.get("decision") or p.get("status") or "?"
        m = {"accepted": "принята", "rejected": "отклонена", "orphan": "канал удалён",
             "closed_by_staff": "закрыта стаффом", "closed_by_site": "закрыта с сайта", "open": "открыта"}
        dec = m.get(dec, dec)
        lines.append(f"#{p['number']} · {CATEGORY_LABELS.get(p['category'], p['category'])} · {dt} · {dec}")
    return "\n".join(lines)[:1024]

async def save_transcript(channel, ticket):
    if channel.guild is None:
        return
    transcript_channel_id = _config_id(channel.guild, "transcript_channel", TRANSCRIPT_CHANNEL_ID)
    if not transcript_channel_id:
        return
    lines = []
    try:
        async for msg in channel.history(limit=500, oldest_first=True):
            content = msg.content or ""
            for att in msg.attachments:
                content = (content + " " if content else "") + att.url
            for emb in msg.embeds:
                title = emb.title or emb.description or ""
                if title:
                    content = (content + " " if content else "") + f"[embed] {title}"
            lines.append(f"[{msg.created_at.strftime('%d.%m.%Y %H:%M')}] {msg.author}: {content}".rstrip())
    except Exception:
        log.exception("Не удалось прочитать историю тикета")
        return
    if not lines:
        return
    t = ticket or {}
    number = t.get("number", "?")
    header = (f"Тикет #{number} · {CATEGORY_LABELS.get(t.get('category'), t.get('category') or '?')}\n"
              f"Открыл: {t.get('owner_name', '?')} ({t.get('owner_id', '?')})\n" + "-" * 40)
    body = (header + "\n" + "\n".join(lines)).encode("utf-8")
    target = channel.guild.get_channel(transcript_channel_id)
    if target is None:
        log.warning("transcript_channel=%s не найден", transcript_channel_id)
        return
    try:
        await target.send(content=f"📄 Транскрипт тикета #{number}",
                          file=discord.File(io.BytesIO(body), filename=f"ticket-{number}.txt"))
    except Exception:
        log.exception("Не удалось отправить транскрипт")

async def create_ticket(interaction, category_key, answers):
    guild = interaction.guild
    if guild is None:
        return
    category = await get_ticket_category(guild)
    staff_role = get_staff_role(guild)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, manage_channels=True),
    }
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True, manage_messages=True)
    ticket_number = next_ticket_number(guild.id)
    channel_name = f"{ticket_number:03d}・{safe_channel_name(interaction.user.name)}"
    channel = await guild.create_text_channel(
        channel_name, category=category, overwrites=overwrites,
        topic=f"FLCN_TICKET|type={category_key}|number={ticket_number}|owner={interaction.user.id}",
        reason=f"FLCN ticket #{ticket_number}")
    record_ticket_open(guild.id, ticket_number, category_key, interaction.user, channel.id)

    embed = discord.Embed(title=f"Тикет №{ticket_number} — {CATEGORY_LABELS.get(category_key, 'Обращение')}",
                          color=discord.Color.blurple())
    embed.add_field(name="Номер тикета", value=f"`#{ticket_number}`", inline=True)
    embed.add_field(name="Категория", value=CATEGORY_LABELS.get(category_key, "Обращение"), inline=True)
    embed.add_field(name="Открыл", value=f"{interaction.user.mention} (`{interaction.user.display_name}`)", inline=False)
    embed.add_field(name="Discord username", value=f"`{interaction.user.name}`", inline=True)
    embed.add_field(name="Discord ID", value=f"`{interaction.user.id}`", inline=True)
    for question, answer in answers:
        answer = (answer or "").strip() or "—"
        if len(answer) > 1024:
            answer = answer[:1018] + " […]"
        if len(embed.fields) < 24:
            embed.add_field(name=question[:256], value=answer, inline=False)
    embed.add_field(name="Предыдущие заявки", value=build_history_text(guild, interaction.user.id), inline=False)
    if category_key == "clan":
        embed.set_footer(text="Для набора приложи скрин Steam и ссылку BattleMetrics.")

    view = TicketActionsView()
    await channel.send(content=staff_role.mention if staff_role else None, embed=embed, view=view)
    if category_key == "clan":
        await channel.send(f"{interaction.user.mention} Прекрепи сюда скрин Steam и ссылку на BattleMetrics , ответы я не умею читать")
    try:
        await interaction.followup.send(f"Готово! Твоя заявка создана: {channel.mention}", ephemeral=True)
    except Exception:
        pass
    log_action(guild.id, "ticket_open", actor=interaction.user, details=f"#{ticket_number} {category_key}")
    await send_staff_log(guild, make_log_embed("📩 Новый тикет", discord.Color.blurple(),
        [("Канал", channel.mention), ("Автор", f"{interaction.user} (`{interaction.user.id}`)"),
         ("Категория", CATEGORY_LABELS.get(category_key, category_key)), ("Номер", f"#{ticket_number}")]))
    asyncio.create_task(push_tickets(guild))

async def start_application(interaction, category_key):
    questions = load_questions(category_key)
    if not questions:
        return await interaction.response.send_message("Анкета пока не настроена.", ephemeral=True)
    answers = []

    async def show_batch(inter, start):
        batch = questions[start:start + 5]
        class BatchModal(discord.ui.Modal, title=f"Заявка ({start + 1}–{start + len(batch)})"):
            def __init__(self):
                super().__init__(timeout=600)
                self.inputs = []
                for q in batch:
                    field = discord.ui.TextInput(label=q[:45], placeholder="Напиши ответ…"[:100],
                        style=discord.TextStyle.paragraph, required=True, min_length=1, max_length=1000)
                    self.inputs.append((q, field))
                    self.add_item(field)
            async def on_submit(self, modal_interaction):
                answers.extend((q, str(f.value)) for q, f in self.inputs)
                next_start = start + len(batch)
                if next_start < len(questions):
                    await show_batch(modal_interaction, next_start)
                else:
                    await modal_interaction.response.defer(ephemeral=True, thinking=True)
                    await create_ticket(modal_interaction, category_key, answers)
        await inter.response.send_modal(BatchModal())
    await show_batch(interaction, 0)

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Закрыть тикет", style=discord.ButtonStyle.danger, custom_id="flcn:close_ticket")
    async def close_ticket(self, interaction, button):
        if not is_staff_interaction(interaction):
            return await interaction.response.send_message("Закрывать тикеты может только Ticket Staff или администратор.", ephemeral=True)
        t = get_ticket_by_channel(interaction.channel.id)
        if t and t["status"] == "open":
            record_ticket_decision(interaction.channel.id, "closed_by_staff", interaction.user)
            log_action(interaction.guild.id, "ticket_close", actor=interaction.user, details=f"#{t['number']}")
            await send_staff_log(interaction.guild, make_log_embed("🔒 Тикет закрыт", discord.Color.dark_gray(),
                [("Номер", f"#{t['number']}"), ("Кем", f"{interaction.user} (`{interaction.user.id}`)")]))
        await interaction.response.send_message("Закрываю тикет…", ephemeral=True)
        await save_transcript(interaction.channel, t)
        await interaction.channel.delete(reason=f"Closed by {interaction.user}")
        asyncio.create_task(push_tickets(interaction.guild))

class TicketActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Взять", style=discord.ButtonStyle.secondary, custom_id="flcn:claim")
    async def claim(self, interaction, button):
        if not is_staff_interaction(interaction):
            return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        t = get_ticket_by_channel(interaction.channel.id)
        if not t or t["status"] != "open":
            return await interaction.response.send_message("Эта заявка уже обработана.", ephemeral=True)
        claim_ticket(interaction.channel.id, interaction.user)
        log_action(interaction.guild.id, "ticket_claim", actor=interaction.user, details=f"#{t['number']}")
        await interaction.response.send_message(f"✅ {interaction.user.mention} взял тикет #{t['number']} в работу.")

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.success, custom_id="flcn:accept")
    async def accept(self, interaction, button):
        if not is_staff_interaction(interaction):
            return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        t = get_ticket_by_channel(interaction.channel.id)
        if not t or t["status"] != "open":
            return await interaction.response.send_message("Эта заявка уже обработана.", ephemeral=True)
        if t["category"] != "clan":
            return await interaction.response.send_message("Кнопка доступна только для заявок в клан.", ephemeral=True)
        clan_role = interaction.guild.get_role(CLAN_ROLE_ID) if CLAN_ROLE_ID else None
        if clan_role is None:
            return await interaction.response.send_message("Роль клана не настроена (CLAN_ROLE_ID).", ephemeral=True)
        member = interaction.guild.get_member(int(t["owner_id"]))
        if member is None:
            return await interaction.response.send_message("Игрок уже не на сервере.", ephemeral=True)
        try:
            await member.add_roles(clan_role, reason=f"FLCN ticket #{t['number']}")
        except discord.Forbidden:
            return await interaction.response.send_message("Не удалось выдать роль клана (нет прав у бота).", ephemeral=True)
        record_ticket_decision(interaction.channel.id, "accepted", interaction.user)
        log_action(interaction.guild.id, "ticket_accept", actor=interaction.user, target=member, details=f"#{t['number']}")
        await interaction.channel.send(f"✅ {member.mention} принят в клан. Решение принял {interaction.user.mention}.")
        await send_staff_log(interaction.guild, make_log_embed("✅ Заявка принята", discord.Color.green(),
            [("Номер", f"#{t['number']}"), ("Игрок", f"{member} (`{member.id}`)"),
             ("Принял", f"{interaction.user} (`{interaction.user.id}`)")]))
        await interaction.response.send_message("Принято, роль выдана, тикет закрывается…", ephemeral=True)
        await asyncio.sleep(2)
        await save_transcript(interaction.channel, t)
        try:
            await interaction.channel.delete(reason="Accepted")
        except Exception:
            pass
        asyncio.create_task(push_tickets(interaction.guild))

    @discord.ui.button(label="Вызвать на обзвон", style=discord.ButtonStyle.primary, custom_id="flcn:call")
    async def call(self, interaction, button):
        if not is_staff_interaction(interaction):
            return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        t = get_ticket_by_channel(interaction.channel.id)
        if not t or t["status"] != "open":
            return await interaction.response.send_message("Эта заявка уже обработана.", ephemeral=True)
        if t["category"] != "clan":
            return await interaction.response.send_message("Кнопка доступна только для заявок в клан.", ephemeral=True)
        channels = list(interaction.guild.voice_channels)
        if VOICE_CATEGORY_NAME:
            cat = discord.utils.get(interaction.guild.categories, name=VOICE_CATEGORY_NAME)
            if cat:
                channels = [c for c in channels if c.category_id == cat.id]
        channels = channels[:25]
        if not channels:
            return await interaction.response.send_message("Нет доступных голосовых каналов.", ephemeral=True)
        await interaction.response.send_message(
            "Выберите голосовой канал для обзвона:",
            view=VoiceSelectView(channels, int(t["owner_id"]), t["number"], interaction.channel.id),
            ephemeral=True)

    @discord.ui.button(label="Отклонить", style=discord.ButtonStyle.danger, custom_id="flcn:reject")
    async def reject(self, interaction, button):
        if not is_staff_interaction(interaction):
            return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        t = get_ticket_by_channel(interaction.channel.id)
        if not t or t["status"] != "open":
            return await interaction.response.send_message("Эта заявка уже обработана.", ephemeral=True)
        if t["category"] != "clan":
            return await interaction.response.send_message("Кнопка доступна только для заявок в клан.", ephemeral=True)
        await interaction.response.send_modal(RejectModal(int(t["owner_id"]), t["number"]))

class VoiceSelectView(discord.ui.View):
    def __init__(self, channels, owner_id, ticket_number, ticket_channel_id):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.ticket_number = ticket_number
        self.ticket_channel_id = ticket_channel_id
        options = [discord.SelectOption(label=c.name[:100], value=str(c.id)) for c in channels]
        select = discord.ui.Select(placeholder="Выберите голосовой канал для обзвона", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction):
        if not is_staff_interaction(interaction):
            return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        voice_id = int(interaction.data["values"][0])
        voice = interaction.guild.get_channel(voice_id)
        if voice is None:
            return await interaction.response.send_message("Канал не найден.", ephemeral=True)
        ticket_channel = interaction.guild.get_channel(self.ticket_channel_id)
        if ticket_channel is None:
            return await interaction.response.send_message("Тикет-канал не найден.", ephemeral=True)
        invite_url = None
        try:
            invite = await voice.create_invite(max_age=1800, max_uses=1,
                reason=f"FLCN обзвон тикет #{self.ticket_number}", unique=True)
            invite_url = invite.url
        except Exception:
            log.exception("Ошибка создания инвайта")
        member = interaction.guild.get_member(self.owner_id)
        embed = discord.Embed(title="📞 Вызов на обзвон", color=discord.Color.blurple(),
                              timestamp=discord.utils.utcnow())
        embed.add_field(name="Игрок", value=member.mention if member else f"<@{self.owner_id}>", inline=False)
        embed.add_field(name="Сотрудник", value=interaction.user.mention, inline=False)
        embed.add_field(name="Голосовой канал", value=f"🔊 {voice.name}", inline=False)
        if invite_url:
            embed.add_field(name="Ссылка для подключения", value=invite_url, inline=False)
        view = None
        if invite_url:
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Присоединиться к голосовому каналу",
                                             url=invite_url, style=discord.ButtonStyle.link))
        await ticket_channel.send(
            content=f"{member.mention if member else ''} был вызван на **обзвон** модератором {interaction.user.mention} в канал 🔊 {voice.name}",
            embed=embed, view=view)
        log_action(interaction.guild.id, "ticket_call", actor=interaction.user, target=member,
                   details=f"#{self.ticket_number} → {voice.name}")
        await interaction.response.edit_message(content=f"Обзвон отправлен в {ticket_channel.mention}.", view=None)

class RejectModal(discord.ui.Modal, title="Причина отказа"):
    reason = discord.ui.TextInput(label="Причина отказа", style=discord.TextStyle.paragraph,
                                   required=True, max_length=1000,
                                   placeholder="Опиши причину, игрок получит её в ЛС")

    def __init__(self, owner_id, ticket_number):
        super().__init__(timeout=600)
        self.owner_id = owner_id
        self.ticket_number = ticket_number

    async def on_submit(self, interaction):
        reason = str(self.reason.value).strip()
        member = interaction.guild.get_member(self.owner_id)
        record_ticket_decision(interaction.channel.id, "rejected", interaction.user, reason)
        log_action(interaction.guild.id, "ticket_reject", actor=interaction.user, target=member,
                   details=f"#{self.ticket_number}: {reason[:200]}")
        await interaction.channel.send(f"❌ Заявка отклонена. Решение принял {interaction.user.mention}.\n**Причина:** {reason}")
        if member:
            try:
                await member.send(f"Твоя заявка в FALCONS (тикет №{self.ticket_number}) отклонена.\n**Причина:** {reason}")
            except discord.Forbidden:
                pass
        await send_staff_log(interaction.guild, make_log_embed("❌ Заявка отклонена", discord.Color.red(),
            [("Номер", f"#{self.ticket_number}"),
             ("Игрок", f"{member} (`{member.id}`)" if member else "?"),
             ("Отклонил", f"{interaction.user} (`{interaction.user.id}`)"),
             ("Причина", reason)]))
        await interaction.response.send_message("Заявка отклонена. Тикет закрывается…", ephemeral=True)
        await asyncio.sleep(2)
        t = get_ticket_by_channel(interaction.channel.id)
        await save_transcript(interaction.channel, t or {"number": self.ticket_number})
        try:
            await interaction.channel.delete(reason="Rejected")
        except Exception:
            pass
        asyncio.create_task(push_tickets(interaction.guild))

class TicketCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Набор в клан", value="clan", description="Анкета и проверка игрока", emoji="⚔️"),
            discord.SelectOption(label="Партнёрство", value="partner", description="Реклама и сотрудничество", emoji="🤝"),
            discord.SelectOption(label="Вопрос по клану", value="question", description="Вопрос руководству", emoji="💬"),
            discord.SelectOption(label="Жалоба на игрока", value="report", description="Сообщить о нарушении", emoji="🛡️"),
            discord.SelectOption(label="Другое обращение", value="other", description="Другие вопросы", emoji="🔧"),
        ]
        super().__init__(placeholder="Выбери категорию обращения…", min_values=1, max_values=1,
                         options=options, custom_id="flcn:category_select")

    async def callback(self, interaction):
        await start_application(interaction, self.values[0])

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect())

def parse_self_roles():
    roles = []
    for part in SELF_ROLES.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        role_id, label = part.split(":", 1)
        role_id, label = role_id.strip(), label.strip()
        if role_id.isascii() and role_id.isdigit() and label:
            roles.append((int(role_id), label[:80]))
    return roles[:25]

def parse_guild_self_roles(guild):
    raw = get_guild_config(guild.id, "self_roles")
    if raw:
        roles = []
        for part in raw.split(";"):
            part = part.strip()
            if not part:
                continue
            role_id, _, label = part.partition(":")
            role_id = role_id.strip()
            if not (role_id.isascii() and role_id.isdigit()):
                continue
            role = guild.get_role(int(role_id))
            if role is None:
                continue
            label = label.strip() or role.name
            roles.append((int(role_id), label[:80]))
        return roles[:25]
    if not GUILD_ID or guild.id == GUILD_ID:
        return [(rid, label) for rid, label in parse_self_roles() if guild.get_role(rid) is not None]
    return []

class SelfRoleSelect(discord.ui.Select):
    def __init__(self, roles):
        options = [discord.SelectOption(label=label, value=str(rid)) for rid, label in roles]
        super().__init__(placeholder="Выбери роли…", min_values=0, max_values=max(1, len(options)),
                         options=options, custom_id="flcn:selfroles")

    async def callback(self, interaction):
        pass

class SelfRolesView(discord.ui.View):
    def __init__(self, roles):
        super().__init__(timeout=None)
        if roles:
            self.add_item(SelfRoleSelect(roles))

async def promote_newcomer(member, chosen):
    if not chosen:
        return None
    guild = member.guild
    newcomer_id = _config_id(guild, "newcomer_role", NEWCOMER_ROLE_ID)
    main_id = _config_id(guild, "main_role", MAIN_ROLE_ID)
    if not newcomer_id or not main_id:
        return None
    newcomer = guild.get_role(newcomer_id)
    main_role = guild.get_role(main_id)
    if newcomer is None or main_role is None or newcomer not in member.roles:
        return None
    try:
        await member.remove_roles(newcomer, reason="FLCN self-roles")
        if main_role not in member.roles:
            await member.add_roles(main_role, reason="FLCN self-roles")
    except (discord.Forbidden, discord.HTTPException):
        log.exception("self-roles: не удалось перевести новичка в main")
        return None
    return main_role.name

@bot.event
async def on_interaction(interaction):
    if interaction.type is not discord.InteractionType.component:
        return
    data = interaction.data or {}
    if data.get("custom_id") != "flcn:selfroles":
        return
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    member = interaction.user
    chosen = set()
    for v in data.get("values") or []:
        if str(v).isdigit():
            chosen.add(int(v))
    added, removed = [], []
    for role_id, _ in parse_guild_self_roles(interaction.guild):
        role = interaction.guild.get_role(role_id)
        if role is None:
            continue
        try:
            if role_id in chosen and role not in member.roles:
                await member.add_roles(role, reason="FLCN self-roles")
                added.append(role.name)
            elif role_id not in chosen and role in member.roles:
                await member.remove_roles(role, reason="FLCN self-roles")
                removed.append(role.name)
        except (discord.Forbidden, discord.HTTPException):
            log.exception("self-roles: не удалось изменить роль %s", role_id)
    await promote_newcomer(member, chosen)
    parts = []
    if added:
        parts.append("выдано: " + ", ".join(added))
    if removed:
        parts.append("снято: " + ", ".join(removed))
    await interaction.response.send_message(
        "Готово. " + ("; ".join(parts) if parts else "Изменений нет."), ephemeral=True)

@bot.tree.command(name="falcons_stats", description="Обновить счётчики сервера")
async def falcons_stats(interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("Нужно право «Управлять сервером».", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await refresh(interaction.guild)
        await interaction.followup.send("Статистика, staff и тикеты обновлены.", ephemeral=True)
    except Exception:
        log.exception("Manual refresh failed")
        await interaction.followup.send("Ошибка. Смотри логи.", ephemeral=True)

@bot.tree.command(name="setup_server", description="Настроить роли и каналы бота для этого сервера")
@app_commands.describe(staff_role="Роль Ticket Staff этого сервера",
                       clan_role="Роль клана (для счётчика «Клан»)",
                       newcomer_role="Роль новичка (выдаётся при входе на сервер)",
                       main_role="Основная роль (выдаётся новичку после выбора само-роли)",
                       welcome_channel="Канал приветствий",
                       goodbye_channel="Канал прощаний",
                       transcript_channel="Канал для транскриптов тикетов",
                       log_channel="Канал для логов стаффа",
                       meeting_channel="Канал для пингов собрания")
async def setup_server(interaction,
                       staff_role: discord.Role = None,
                       clan_role: discord.Role = None,
                       newcomer_role: discord.Role = None,
                       main_role: discord.Role = None,
                       welcome_channel: discord.TextChannel = None,
                       goodbye_channel: discord.TextChannel = None,
                       transcript_channel: discord.TextChannel = None,
                       log_channel: discord.TextChannel = None,
                       meeting_channel: discord.TextChannel = None):
    if interaction.guild is None:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    if not has_staff_perms(interaction.user):
        return await interaction.response.send_message("Нужны права стаффа (или «Управлять сервером»).", ephemeral=True)
    mapping = [("staff_role", staff_role), ("clan_role", clan_role),
               ("newcomer_role", newcomer_role), ("main_role", main_role),
               ("welcome_channel", welcome_channel), ("goodbye_channel", goodbye_channel),
               ("transcript_channel", transcript_channel), ("log_channel", log_channel),
               ("meeting_channel", meeting_channel)]
    saved = []
    for key, value in mapping:
        if value is not None:
            set_guild_config(interaction.guild.id, key, str(value.id))
            saved.append(key)
    current = _current_guild_config(interaction.guild)
    lines = []
    for key, label in (("staff_role", "Стафф-роль"), ("clan_role", "Клан-роль"),
                       ("newcomer_role", "Роль новичка"), ("main_role", "MAIN-роль"),
                       ("welcome_channel", "Приветствия"), ("goodbye_channel", "Прощания"),
                       ("transcript_channel", "Транскрипты"), ("log_channel", "Логи"),
                       ("meeting_channel", "Собрание")):
        lines.append(f"**{label}:** {current.get(key) or '— (не задано)'}")
    lines.append(f"**Само-роли:** {len(parse_guild_self_roles(interaction.guild))} шт. (добавить: /selfrole_add)")
    if saved:
        lines.append("")
        lines.append(f"Обновлено: {', '.join(saved)}")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@bot.tree.command(name="setup_tickets", description="Создать панель заявок FLCN")
async def setup_tickets(interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Нужна роль Ticket Staff или права.", ephemeral=True)
    embed = discord.Embed(title="FALCONS SUPPORT",
        description=("**Выбери категорию обращения в меню ниже.**\n\n"
                     "⚔️ **Набор в клан** — от 15 лет и от 3000 часов в Rust.\n"
                     "Скриншот Steam понадобится только для заявки в клан."),
        color=discord.Color.blurple())
    file = discord.File("falcons.png", filename="falcons.png")
    embed.set_image(url="attachment://falcons.png")
    await interaction.channel.send(file=file, embed=embed, view=TicketPanelView())
    await interaction.response.send_message("Панель заявок опубликована.", ephemeral=True)

@bot.tree.command(name="setup_roles", description="Опубликовать панель само-ролей")
async def setup_roles(interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
    roles = parse_guild_self_roles(interaction.guild)
    if not roles:
        return await interaction.response.send_message(
            "Само-роли для этого сервера не настроены. Добавь их командой /selfrole_add.", ephemeral=True)
    embed = discord.Embed(title="🎭 Само-роли", color=discord.Color.blurple(),
                          description="Выбери роли в меню ниже. Можно выбрать несколько или снять уже выбранные.")
    embed.add_field(name="Доступные роли", value="\n".join(label for _, label in roles)[:1024], inline=False)
    await interaction.channel.send(embed=embed, view=SelfRolesView(roles))
    await interaction.response.send_message("Панель само-ролей опубликована.", ephemeral=True)

@bot.tree.command(name="selfrole_add", description="Добавить роль в панель само-ролей этого сервера")
@app_commands.describe(role="Роль, которую смогут брать участники")
async def selfrole_add(interaction, role: discord.Role):
    if interaction.guild is None:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    if not has_staff_perms(interaction.user):
        return await interaction.response.send_message("Нужны права стаффа (или «Управлять сервером»).", ephemeral=True)
    current = get_guild_config(interaction.guild.id, "self_roles") or ""
    if not current and (not GUILD_ID or interaction.guild.id == GUILD_ID):
        current = ";".join(str(rid) for rid, _ in parse_self_roles() if interaction.guild.get_role(rid))
    parts = [p.strip() for p in current.split(";") if p.strip()]
    if str(role.id) in {p.partition(":")[0].strip() for p in parts}:
        return await interaction.response.send_message("Эта роль уже в панели.", ephemeral=True)
    if len(parts) >= 25:
        return await interaction.response.send_message("Максимум 25 ролей в панели.", ephemeral=True)
    parts.append(str(role.id))
    set_guild_config(interaction.guild.id, "self_roles", ";".join(parts))
    await interaction.response.send_message(
        f"Добавил {role.mention}. Всего: {len(parts)}. Запусти /setup_roles, чтобы опубликовать панель.", ephemeral=True)

@bot.tree.command(name="selfrole_remove", description="Убрать роль из панели само-ролей этого сервера")
@app_commands.describe(role="Роль, которую убрать из панели")
async def selfrole_remove(interaction, role: discord.Role):
    if interaction.guild is None:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    if not has_staff_perms(interaction.user):
        return await interaction.response.send_message("Нужны права стаффа (или «Управлять сервером»).", ephemeral=True)
    current = get_guild_config(interaction.guild.id, "self_roles") or ""
    if not current and (not GUILD_ID or interaction.guild.id == GUILD_ID):
        current = ";".join(str(rid) for rid, _ in parse_self_roles() if interaction.guild.get_role(rid))
    parts = [p.strip() for p in current.split(";") if p.strip()]
    kept = [p for p in parts if p.partition(":")[0].strip() != str(role.id)]
    if len(kept) == len(parts):
        return await interaction.response.send_message("Этой роли нет в панели.", ephemeral=True)
    set_guild_config(interaction.guild.id, "self_roles", ";".join(kept))
    await interaction.response.send_message(
        f"Убрал {role.mention}. Осталось: {len(kept)}. Запусти /setup_roles, чтобы обновить панель.", ephemeral=True)

@bot.tree.command(name="ticket_add_question", description="Добавить вопрос в анкету")
@app_commands.describe(category="clan / partner / question / report / other", question="Текст вопроса")
async def ticket_add_question(interaction, category: str, question: str):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Нет прав.", ephemeral=True)
    if category not in CATEGORY_QUESTIONS:
        return await interaction.response.send_message("Категория: clan, partner, question, report, other.", ephemeral=True)
    qs = load_questions(category)
    qs.append(question.strip())
    save_questions(category, qs)
    await interaction.response.send_message(f"Добавил. Всего: {len(qs)}.", ephemeral=True)

@bot.tree.command(name="ticket_remove_question", description="Удалить вопрос")
@app_commands.describe(category="clan / partner / question / report / other", number="Номер вопроса")
async def ticket_remove_question(interaction, category: str, number: int):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Нет прав.", ephemeral=True)
    if category not in CATEGORY_QUESTIONS:
        return await interaction.response.send_message("Неверная категория.", ephemeral=True)
    qs = load_questions(category)
    if number < 1 or number > len(qs):
        return await interaction.response.send_message(f"Номер 1..{len(qs)}.", ephemeral=True)
    removed = qs.pop(number - 1)
    save_questions(category, qs)
    await interaction.response.send_message(f"Удалил: {removed}", ephemeral=True)

@bot.tree.command(name="ticket_questions", description="Показать вопросы категории")
@app_commands.describe(category="clan / partner / question / report / other")
async def ticket_questions(interaction, category: str):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Нет прав.", ephemeral=True)
    if category not in CATEGORY_QUESTIONS:
        return await interaction.response.send_message("Неверная категория.", ephemeral=True)
    qs = load_questions(category)
    body = "\n".join(f"{i}. {q}" for i, q in enumerate(qs, 1)) or "Нет."
    await interaction.response.send_message(f"**{CATEGORY_LABELS[category]}**\n{body}"[:1950], ephemeral=True)

@bot.tree.command(name="ticket_debug", description="Проверить настройки тикетов")
async def ticket_debug(interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("Нужны права.", ephemeral=True)
    guild = interaction.guild
    role = get_staff_role(guild)
    category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
    lines = [
        f"TICKET_STAFF_ROLE_ID: `{TICKET_STAFF_ROLE_ID}`",
        f"Роль найдена: {'да — ' + role.name if role else 'НЕТ'}",
        f"Категория тикетов: {'есть' if category else 'нет'}",
        f"Manage Channels: {'да' if guild.me and guild.me.guild_permissions.manage_channels else 'нет'}",
        f"CLAN_ROLE_ID: `{CLAN_ROLE_ID}`",
        f"OWNER_ID: `{OWNER_ID}`",
        f"STAFF_LOG_CHANNEL_ID: `{STAFF_LOG_CHANNEL_ID}`",
    ]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@bot.tree.command(name="ticket_repair_permissions", description="Починить права стаффа в тикетах")
async def ticket_repair_permissions(interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("Нужны права.", ephemeral=True)
    role = get_staff_role(interaction.guild)
    if role is None:
        return await interaction.response.send_message("Роль Ticket Staff не найдена.", ephemeral=True)
    category = await get_ticket_category(interaction.guild)
    repaired = 0
    for channel in category.text_channels:
        try:
            await channel.set_permissions(role, view_channel=True, send_messages=True,
                read_message_history=True, attach_files=True, embed_links=True, manage_messages=True,
                reason="Repair permissions")
            repaired += 1
        except discord.Forbidden:
            pass
    await interaction.response.send_message(f"Права обновлены в {repaired} тикетах.", ephemeral=True)

async def fetch_bm_hours(player_id):
    if not str(player_id).isdigit():
        return None
    url = f"https://api.battlemetrics.com/players/{player_id}"
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return None
                data = await r.json()
    except Exception:
        log.exception("BattleMetrics: запрос не удался")
        return None
    attrs = ((data or {}).get("data") or {}).get("attributes") or {}
    seconds = attrs.get("playtime")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds < 0:
        return None
    return int(seconds // 3600)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    channel = message.channel
    is_clan_ticket = (isinstance(channel, discord.TextChannel) and bool(channel.topic)
                      and "FLCN_TICKET|type=clan|" in channel.topic)
    if is_clan_ticket:
        bm_match = re.search(r"https?://(?:www\.)?battlemetrics\.com/players/(\d+)(?:[^\s]*)?", message.content, re.IGNORECASE)
        steam_match = re.search(r"https?://(?:www\.)?steamcommunity\.com/(?:profiles/\d+|id/[^/\s?#]+)(?:[^\s]*)?", message.content, re.IGNORECASE)
        replies = []
        if steam_match:
            replies.append(f"Нашёл Steam-профиль: {steam_match.group(0).rstrip('.,)>]}')}")
        if bm_match:
            hours = await fetch_bm_hours(bm_match.group(1))
            if hours is None:
                replies.append(f"Нашёл BattleMetrics ID `{bm_match.group(1)}`, но часы не удалось получить.")
            elif hours >= RUST_MIN_HOURS:
                replies.append(f"✅ BattleMetrics: **{hours} ч** — порог {RUST_MIN_HOURS} ч пройден.")
            else:
                replies.append(f"⚠️ BattleMetrics: **{hours} ч** — меньше порога {RUST_MIN_HOURS} ч.")
        if replies:
            await message.reply("\n\n".join(replies), mention_author=False)
    await bot.process_commands(message)

def _check_secret(request):
    if not API_SECRET_KEY:
        return False
    return request.headers.get("X-Falcons-Secret", "") == API_SECRET_KEY

def _get_guild_and_actor(data):
    guild = _resolve_guild(data.get("guild_id"))
    if guild is None:
        return None, None, None
    actor = guild.get_member(int(data.get("actor_id", 0)))
    channel = guild.get_channel(int(data.get("channel_id", 0)))
    return guild, actor, channel

def _resolve_guild(guild_id):
    gid = str(guild_id or "").strip()
    if gid.isascii() and gid.isdigit():
        guild = bot.get_guild(int(gid))
        if guild is not None:
            return guild
    return bot.get_guild(GUILD_ID) if GUILD_ID else (bot.guilds[0] if bot.guilds else None)

async def http_close_ticket(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    data = await request.json()
    guild, actor, channel = _get_guild_and_actor(data)
    if guild is None or channel is None:
        return aiohttp_web.json_response({"ok": False, "error": "not found"}, status=404)
    if actor is None or not has_staff_perms(actor):
        return aiohttp_web.json_response({"ok": False, "error": "no perms"}, status=403)
    t = get_ticket_by_channel(channel.id)
    if t and t["status"] == "open":
        record_ticket_decision(channel.id, "closed_by_site", actor)
        log_action(guild.id, "ticket_close_site", actor=actor, details=f"#{t['number']}")
    await save_transcript(channel, t)
    try:
        await channel.delete(reason=f"Closed from site by {actor}")
    except Exception as e:
        return aiohttp_web.json_response({"ok": False, "error": str(e)}, status=500)
    asyncio.create_task(push_tickets(guild))
    return aiohttp_web.json_response({"ok": True})

async def http_accept_ticket(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    data = await request.json()
    guild, actor, channel = _get_guild_and_actor(data)
    if guild is None or channel is None:
        return aiohttp_web.json_response({"ok": False, "error": "not found"}, status=404)
    if actor is None or not has_staff_perms(actor):
        return aiohttp_web.json_response({"ok": False, "error": "no perms"}, status=403)
    t = get_ticket_by_channel(channel.id)
    if not t or t["status"] != "open":
        return aiohttp_web.json_response({"ok": False, "error": "already decided"}, status=409)
    clan_role = guild.get_role(CLAN_ROLE_ID) if CLAN_ROLE_ID else None
    member = guild.get_member(int(t["owner_id"]))
    if clan_role is None or member is None:
        return aiohttp_web.json_response({"ok": False, "error": "role or member missing"}, status=400)
    try:
        await member.add_roles(clan_role, reason=f"Accepted from site by {actor}")
    except discord.Forbidden:
        return aiohttp_web.json_response({"ok": False, "error": "forbidden"}, status=403)
    record_ticket_decision(channel.id, "accepted", actor)
    log_action(guild.id, "ticket_accept_site", actor=actor, target=member, details=f"#{t['number']}")
    try:
        await channel.send(f"✅ {member.mention} принят в клан. Решение принял {actor.mention}.")
        await save_transcript(channel, t)
        await asyncio.sleep(2)
        await channel.delete(reason="Accepted from site")
    except Exception:
        pass
    asyncio.create_task(push_tickets(guild))
    return aiohttp_web.json_response({"ok": True})

async def http_reject_ticket(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    data = await request.json()
    reason = str(data.get("reason", "")).strip()
    if not reason:
        return aiohttp_web.json_response({"ok": False, "error": "reason required"}, status=400)
    guild, actor, channel = _get_guild_and_actor(data)
    if guild is None or channel is None:
        return aiohttp_web.json_response({"ok": False, "error": "not found"}, status=404)
    if actor is None or not has_staff_perms(actor):
        return aiohttp_web.json_response({"ok": False, "error": "no perms"}, status=403)
    t = get_ticket_by_channel(channel.id)
    if not t or t["status"] != "open":
        return aiohttp_web.json_response({"ok": False, "error": "already decided"}, status=409)
    member = guild.get_member(int(t["owner_id"]))
    record_ticket_decision(channel.id, "rejected", actor, reason)
    log_action(guild.id, "ticket_reject_site", actor=actor, target=member, details=f"#{t['number']}: {reason[:200]}")
    try:
        await channel.send(f"❌ Заявка отклонена {actor.mention}.\n**Причина:** {reason}")
        if member:
            try:
                await member.send(f"Твоя заявка в FALCONS (тикет №{t['number']}) отклонена.\n**Причина:** {reason}")
            except discord.Forbidden:
                pass
        await save_transcript(channel, t)
        await asyncio.sleep(2)
        await channel.delete(reason="Rejected from site")
    except Exception:
        pass
    asyncio.create_task(push_tickets(guild))
    return aiohttp_web.json_response({"ok": True})

async def http_get_questions(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    questions = {cat: load_questions(cat) for cat in CATEGORY_QUESTIONS}
    return aiohttp_web.json_response({"ok": True, "questions": questions})

async def http_set_questions(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(data, dict):
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)
    category = data.get("category")
    if category not in CATEGORY_QUESTIONS:
        return aiohttp_web.json_response({"ok": False, "error": "invalid category"}, status=400)
    raw = data.get("questions")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 30:
        return aiohttp_web.json_response({"ok": False, "error": "invalid questions"}, status=400)
    questions = []
    for q in raw:
        if not isinstance(q, str):
            return aiohttp_web.json_response({"ok": False, "error": "invalid question"}, status=400)
        q = q.strip()
        if not q or len(q) > 500:
            return aiohttp_web.json_response({"ok": False, "error": "invalid question"}, status=400)
        questions.append(q)
    save_questions(category, questions)
    log.info("Анкета %s обновлена с сайта: %s вопросов", category, len(questions))
    return aiohttp_web.json_response({"ok": True, "count": len(questions)})

async def http_reload_config(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    log.info("Конфиг перезагружен с сайта")
    guild = bot.get_guild(GUILD_ID) if GUILD_ID else (bot.guilds[0] if bot.guilds else None)
    if guild is not None:
        try:
            await send_staff_log(guild, make_log_embed("🔄 Конфиг перезагружен", discord.Color.blurple(),
                [("Источник", "Панель управления (сайт)")]))
        except Exception:
            log.exception("reload_config: не удалось отправить лог")
    return aiohttp_web.json_response({"ok": True})

async def http_get_punishments(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    user_id = request.query.get("user_id", "").strip()
    if not (user_id.isascii() and user_id.isdigit()):
        return aiohttp_web.json_response({"ok": False, "error": "invalid user_id"}, status=400)
    try:
        limit = max(1, min(int(request.query.get("limit", "10")), 50))
    except (TypeError, ValueError):
        limit = 10
    guild = _resolve_guild(request.query.get("guild_id"))
    if guild is None:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)
    member = guild.get_member(int(user_id))
    return aiohttp_web.json_response({
        "ok": True,
        "user_name": str(member) if member else None,
        "warns": count_warns(guild.id, user_id),
        "punishments": get_user_punishments(guild.id, user_id, limit=limit),
    })

async def http_punish(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(data, dict):
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)
    action = data.get("action")
    if action not in ("warn", "timeout", "kick", "ban"):
        return aiohttp_web.json_response({"ok": False, "error": "invalid action"}, status=400)
    user_id = str(data.get("user_id", "")).strip()
    if not (user_id.isascii() and user_id.isdigit()):
        return aiohttp_web.json_response({"ok": False, "error": "invalid user_id"}, status=400)
    reason = str(data.get("reason", "")).strip()
    if not reason or len(reason) > 500:
        return aiohttp_web.json_response({"ok": False, "error": "invalid reason"}, status=400)
    duration_minutes = data.get("duration_minutes")
    if action == "timeout":
        if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int) or not 1 <= duration_minutes <= 40320:
            return aiohttp_web.json_response({"ok": False, "error": "invalid duration"}, status=400)
    else:
        duration_minutes = None
    guild, actor, _ = _get_guild_and_actor(data)
    if guild is None:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)
    if actor is None or not has_staff_perms(actor):
        return aiohttp_web.json_response({"ok": False, "error": "no perms"}, status=403)
    member = guild.get_member(int(user_id))
    if member is None:
        return aiohttp_web.json_response({"ok": False, "error": "member not found"}, status=400)
    if member.id == actor.id:
        return aiohttp_web.json_response({"ok": False, "error": "self"}, status=400)
    if action in ("timeout", "kick", "ban"):
        if guild.me and member.id != guild.owner_id and member.top_role >= guild.me.top_role:
            return aiohttp_web.json_response({"ok": False, "error": "role too high"}, status=403)
        if (isinstance(actor, discord.Member) and actor.id not in (guild.owner_id, OWNER_ID)
                and actor.top_role <= member.top_role):
            return aiohttp_web.json_response({"ok": False, "error": "target role higher"}, status=403)
    ok, error = await _do_punishment(guild, actor, member, action, reason, duration_minutes)
    if not ok:
        if error == "forbidden":
            return aiohttp_web.json_response({"ok": False, "error": "forbidden"}, status=403)
        return aiohttp_web.json_response({"ok": False, "error": "discord api"}, status=502)
    return aiohttp_web.json_response({"ok": True})

async def http_invite_reward(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(data, dict):
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)
    user_id = str(data.get("user_id", "")).strip()
    if not (user_id.isascii() and user_id.isdigit()):
        return aiohttp_web.json_response({"ok": False, "error": "invalid user_id"}, status=400)
    guild, actor, _ = _get_guild_and_actor(data)
    if guild is None:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)
    if actor is None or not has_staff_perms(actor):
        return aiohttp_web.json_response({"ok": False, "error": "no perms"}, status=403)
    member = guild.get_member(int(user_id))
    if member is None:
        return aiohttp_web.json_response({"ok": False, "error": "member not found"}, status=400)
    total = count_valid_invites(guild.id, member.id)
    awarded = await award_invite_rewards(guild, member)
    return aiohttp_web.json_response({"ok": True, "invites": total, "awarded": awarded})

async def http_bot_status(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    latency = bot.latency
    latency_ms = None
    if isinstance(latency, (int, float)) and latency == latency and latency > 0:
        latency_ms = round(latency * 1000)
    return aiohttp_web.json_response({
        "ok": True,
        "user": str(bot.user) if bot.user else None,
        "latency_ms": latency_ms,
        "guilds": [{"id": str(g.id), "name": g.name, "members": g.member_count or 0} for g in bot.guilds],
    })

def _current_settings():
    return {
        "welcome_enabled": setting_bool("welcome_enabled", True),
        "goodbye_enabled": setting_bool("goodbye_enabled", GOODBYE_ENABLED),
        "welcome_text": get_setting("welcome_text") or WELCOME_TEXT,
        "goodbye_text": get_setting("goodbye_text") or GOODBYE_TEXT,
    }

async def http_get_settings(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    return aiohttp_web.json_response({"ok": True, "settings": _current_settings()})

async def http_set_settings(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(data, dict):
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)
    updates = {}
    for key in ("welcome_enabled", "goodbye_enabled"):
        if key in data:
            if not isinstance(data[key], bool):
                return aiohttp_web.json_response({"ok": False, "error": f"invalid {key}"}, status=400)
            updates[key] = "1" if data[key] else "0"
    for key in ("welcome_text", "goodbye_text"):
        if key in data:
            value = data[key]
            if not isinstance(value, str) or len(value) > 500:
                return aiohttp_web.json_response({"ok": False, "error": f"invalid {key}"}, status=400)
            updates[key] = value.strip()
    if not updates:
        return aiohttp_web.json_response({"ok": False, "error": "no settings"}, status=400)
    for key, value in updates.items():
        set_setting(key, value)
    log.info("Настройки бота обновлены с сайта: %s", sorted(updates))
    guild = bot.get_guild(GUILD_ID) if GUILD_ID else (bot.guilds[0] if bot.guilds else None)
    if guild is not None:
        current = _current_settings()
        fields = [("Источник", "Панель управления (сайт)"),
                  ("Приветствие", "вкл" if current["welcome_enabled"] else "выкл"),
                  ("Прощание", "вкл" if current["goodbye_enabled"] else "выкл")]
        if "welcome_text" in updates:
            fields.append(("Текст приветствия", updates["welcome_text"] or "(из .env)"))
        if "goodbye_text" in updates:
            fields.append(("Текст прощания", updates["goodbye_text"] or "(из .env)"))
        try:
            await send_staff_log(guild, make_log_embed("⚙️ Настройки бота обновлены", discord.Color.blurple(), fields))
        except Exception:
            log.exception("settings: не удалось отправить лог")
    return aiohttp_web.json_response({"ok": True, "settings": _current_settings()})

_meetings_running = set()

async def run_meeting(guild, voice, actor):
    try:
        log.info("Собрание: старт, guild %s, войс %s", guild.id, voice.id)
        clan_role_id = _config_id(guild, "clan_role", CLAN_ROLE_ID)
        clan_role = guild.get_role(clan_role_id) if clan_role_id else None
        if clan_role is None:
            clan_role = discord.utils.find(lambda r: r.name.casefold() == "clan", guild.roles)
        ping_mention = clan_role.mention if clan_role is not None else "@everyone"
        meeting_channel_id = _config_id(guild, "meeting_channel", MEETING_CHANNEL_ID)
        meeting_channel = guild.get_channel(meeting_channel_id) if meeting_channel_id else None
        if meeting_channel is None:
            log.warning("Собрание: канал пингов не найден (id %s), guild %s", meeting_channel_id, guild.id)
        else:
            for i in range(3):
                if i:
                    await asyncio.sleep(60)
                try:
                    await meeting_channel.send(
                        f"🚨 {ping_mention} Собрание клана! Заходим в войс: 🔊 {voice.name}",
                        allowed_mentions=discord.AllowedMentions(everyone=True, roles=True))
                    log.info("Собрание: пинг %s/3 отправлен (%s), guild %s", i + 1, ping_mention, guild.id)
                except discord.HTTPException as e:
                    if e.status == 429:
                        log.warning("Собрание: пинги остановлены (rate limit), guild %s", guild.id)
                    else:
                        log.exception("Собрание: пинг не отправлен")
                    break
        candidates = []
        if clan_role is not None:
            for m in guild.members:
                if m.bot or clan_role not in m.roles:
                    continue
                if m.status in (discord.Status.offline, discord.Status.invisible):
                    continue
                if m.voice is not None:
                    continue
                candidates.append(m)
        log.info("Собрание: ЛС-кандидатов %s, guild %s", len(candidates), guild.id)
        sent = 0
        for idx, member in enumerate(candidates):
            if idx:
                await asyncio.sleep(15)
            text = f"🚨 {member.display_name}, собрание клана! Заходим в войс: 🔊 {voice.name}"
            try:
                await member.send(text)
                sent += 1
            except discord.Forbidden:
                log.info("Собрание: ЛС закрыты у %s", member.id)
            except discord.HTTPException as e:
                if e.status == 429:
                    log.warning("Собрание: rate limit на ЛС, пауза 60 сек")
                    await asyncio.sleep(60)
                    try:
                        await member.send(text)
                        sent += 1
                    except Exception:
                        log.exception("Собрание: ЛС не отправлено")
                else:
                    log.exception("Собрание: ЛС не отправлено")
            except Exception:
                log.exception("Собрание: ЛС не отправлено")
        fields = [("Войс-канал", f"🔊 {voice.name} (`{voice.id}`)"),
                  ("Запустил", f"{actor} (`{actor.id}`)"),
                  ("ЛС", f"Отправлено {sent} из {len(candidates)}")]
        log.info("Собрание: ЛС отправлено %s из %s, guild %s", sent, len(candidates), guild.id)
        await send_staff_log(guild, make_log_embed("🚨 Собрание объявлено", discord.Color.red(), fields))
    except Exception:
        log.exception("Собрание: непредвиденная ошибка")
    finally:
        _meetings_running.discard(guild.id)

async def http_meeting(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(data, dict):
        return aiohttp_web.json_response({"ok": False, "error": "invalid json"}, status=400)
    guild = _resolve_guild(data.get("guild_id"))
    if guild is None:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)
    actor_id = str(data.get("actor_id") or "").strip()
    actor = guild.get_member(int(actor_id)) if actor_id.isdigit() else None
    if actor is None or not has_staff_perms(actor):
        return aiohttp_web.json_response({"ok": False, "error": "no perms"}, status=403)
    voice_id = str(data.get("voice_channel_id") or "").strip()
    voice = guild.get_channel(int(voice_id)) if voice_id.isdigit() else None
    if voice is None or not isinstance(voice, discord.VoiceChannel):
        return aiohttp_web.json_response({"ok": False, "error": "invalid voice_channel_id"}, status=400)
    if guild.id in _meetings_running:
        return aiohttp_web.json_response({"ok": False, "error": "meeting already running"}, status=409)
    _meetings_running.add(guild.id)
    asyncio.create_task(run_meeting(guild, voice, actor))
    log.info("Собрание запущено с сайта: guild %s, войс %s, запустил %s", guild.id, voice.id, actor.id)
    return aiohttp_web.json_response({"ok": True})

async def http_voice_channels(request):
    if not _check_secret(request):
        return aiohttp_web.json_response({"ok": False, "error": "unauthorized"}, status=401)
    guild = _resolve_guild(request.query.get("guild_id"))
    if guild is None:
        return aiohttp_web.json_response({"ok": False, "error": "guild not found"}, status=404)
    channels = [{"id": str(c.id), "name": c.name} for c in sorted(guild.voice_channels, key=lambda c: c.position)]
    return aiohttp_web.json_response({"ok": True, "channels": channels})

async def start_bot_http():
    app = aiohttp_web.Application()
    app.router.add_post("/bot/ticket/close", http_close_ticket)
    app.router.add_post("/bot/ticket/accept", http_accept_ticket)
    app.router.add_post("/bot/ticket/reject", http_reject_ticket)
    app.router.add_get("/bot/questions", http_get_questions)
    app.router.add_post("/bot/questions", http_set_questions)
    app.router.add_post("/bot/reload_config", http_reload_config)
    app.router.add_get("/bot/punishments", http_get_punishments)
    app.router.add_post("/bot/punish", http_punish)
    app.router.add_post("/bot/invites/reward", http_invite_reward)
    app.router.add_get("/bot/status", http_bot_status)
    app.router.add_get("/bot/settings", http_get_settings)
    app.router.add_post("/bot/settings", http_set_settings)
    app.router.add_post("/bot/meeting", http_meeting)
    app.router.add_get("/bot/voice-channels", http_voice_channels)
    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    site = aiohttp_web.TCPSite(runner, BOT_HTTP_HOST, BOT_HTTP_PORT)
    await site.start()
    log.info("Bot HTTP API запущен на %s:%s", BOT_HTTP_HOST, BOT_HTTP_PORT)

MOD_ACTION_LABELS = {"warn": "Предупреждение", "timeout": "Таймаут", "untimeout": "Снятие таймаута",
                     "kick": "Кик", "ban": "Бан", "unban": "Разбан"}
MOD_EMBED_TITLES = {"warn": "⚠️ Предупреждение", "timeout": "⏳ Таймаут",
                    "kick": "👢 Кик", "ban": "🔨 Бан"}
MOD_EMBED_COLORS = {"warn": discord.Color.orange(), "timeout": discord.Color.orange(),
                    "kick": discord.Color.dark_orange(), "ban": discord.Color.red()}

def add_punishment(guild_id, user_id, user_name, mod_id, mod_name, action, reason, duration=None, expires_at=None):
    with _db() as conn:
        conn.execute(
            "INSERT INTO punishments(guild_id, user_id, user_name, mod_id, mod_name, action, reason, duration, ts, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (str(guild_id), str(user_id), user_name, str(mod_id) if mod_id else None,
             mod_name, action, reason, duration, int(time.time()), expires_at))

def get_user_punishments(guild_id, user_id, limit=10):
    with _db() as conn:
        rows = conn.execute(
            "SELECT action, reason, mod_name, ts, duration, expires_at FROM punishments WHERE guild_id=? AND user_id=? ORDER BY ts DESC LIMIT ?",
            (str(guild_id), str(user_id), limit)).fetchall()
        return [dict(r) for r in rows]

async def log_mod_action(guild, title, color, target_text, mod, reason, duration_text=None):
    fields = [("Игрок", target_text),
              ("Модератор", f"{mod} (`{mod.id}`)"),
              ("Причина", reason or "—")]
    if duration_text:
        fields.append(("Длительность", duration_text))
    await send_staff_log(guild, make_log_embed(title, color, fields))

async def _do_punishment(guild, mod, member, action, reason, duration_minutes=None, title=None):
    duration = None
    expires_at = None
    duration_text = None
    if action == "timeout":
        duration = int(duration_minutes) * 60
        expires_at = int(time.time()) + duration
        duration_text = f"{duration_minutes} мин"
    dm_messages = {
        "warn": f"Ты получил предупреждение на сервере FALCONS.\n**Причина:** {reason}",
        "timeout": f"Тебе выдан таймаут на {duration_minutes} мин на сервере FALCONS.\n**Причина:** {reason}",
        "kick": f"Ты кикнут с сервера FALCONS.\n**Причина:** {reason}",
        "ban": f"Ты забанен на сервере FALCONS.\n**Причина:** {reason}",
    }
    try:
        await member.send(dm_messages[action])
    except (discord.Forbidden, discord.HTTPException):
        pass
    try:
        if action == "timeout":
            await member.timeout(datetime.timedelta(minutes=duration_minutes), reason=f"{reason} | {mod}")
        elif action == "kick":
            await member.kick(reason=f"{reason} | {mod}")
        elif action == "ban":
            await member.ban(reason=f"{reason} | {mod}")
    except discord.Forbidden:
        return False, "forbidden"
    except discord.HTTPException:
        log.exception("moderation action failed")
        return False, "api"
    add_punishment(guild.id, member.id, str(member), mod.id, str(mod), action, reason, duration, expires_at)
    log_action(guild.id, f"mod_{action}", actor=mod, target=member, details=reason)
    await log_mod_action(guild, title or MOD_EMBED_TITLES[action],
                         discord.Color.orange() if title else MOD_EMBED_COLORS[action],
                         f"{member} (`{member.id}`)", mod, reason, duration_text)
    if action == "warn":
        asyncio.create_task(check_auto_punish(guild, member))
    return True, None

async def apply_punishment(interaction, action, target_id, reason, duration_minutes=None):
    guild = interaction.guild
    mod = interaction.user
    member = guild.get_member(int(target_id))
    if member is None:
        return await interaction.response.send_message("Игрок не найден на сервере.", ephemeral=True)
    if member.id == mod.id:
        return await interaction.response.send_message("Нельзя применить наказание к себе.", ephemeral=True)
    if action in ("timeout", "kick", "ban"):
        if guild.me and member.id != guild.owner_id and member.top_role >= guild.me.top_role:
            return await interaction.response.send_message("Не могу: роль игрока выше или равна роли бота.", ephemeral=True)
        if (isinstance(mod, discord.Member) and mod.id not in (guild.owner_id, OWNER_ID)
                and mod.top_role <= member.top_role):
            return await interaction.response.send_message("Нельзя наказать игрока с ролью выше или равной твоей.", ephemeral=True)
    ok, error = await _do_punishment(guild, mod, member, action, reason, duration_minutes)
    if not ok:
        if error == "forbidden":
            return await interaction.response.send_message("Не удалось выполнить действие: нет прав у бота.", ephemeral=True)
        return await interaction.response.send_message("Ошибка Discord API, действие не выполнено.", ephemeral=True)
    await interaction.response.send_message(f"✅ {MOD_ACTION_LABELS[action]} применён к {member.mention}.", ephemeral=True)

async def check_auto_punish(guild, member):
    warns = count_warns(guild.id, member.id)
    if warns not in (WARN_TIMEOUT_COUNT, WARN_KICK_COUNT):
        return
    if member.bot or member.id in (getattr(guild, "owner_id", 0), OWNER_ID) or has_staff_perms(member):
        return
    if guild.me and member.top_role >= guild.me.top_role:
        return
    reason = f"Авто-наказание: {warns} предупреждений"
    if warns == WARN_TIMEOUT_COUNT:
        action, minutes, title = "timeout", WARN_TIMEOUT_MINUTES, "⏳ Авто-наказание: таймаут"
    else:
        action, minutes, title = "kick", None, "👢 Авто-наказание: кик"
    ok, error = await _do_punishment(guild, bot.user, member, action, reason, minutes, title)
    if not ok:
        log.warning("Авто-наказание не выполнено (%s): %s", error, member.id)
        return
    log_action(guild.id, "auto_punish", target=member, details=reason)

class PunishReasonModal(discord.ui.Modal):
    TITLES = {"warn": "Причина предупреждения", "timeout": "Причина таймаута",
              "kick": "Причина кика", "ban": "Причина бана"}

    def __init__(self, action, target_id, duration_minutes=None):
        super().__init__(title=self.TITLES.get(action, "Причина"), timeout=600)
        self.action = action
        self.target_id = target_id
        self.duration_minutes = duration_minutes
        self.reason = discord.ui.TextInput(label="Причина", style=discord.TextStyle.paragraph,
                                           required=True, min_length=1, max_length=500,
                                           placeholder="Причина наказания (игрок получит её в ЛС)")
        self.add_item(self.reason)

    async def on_submit(self, interaction):
        if not is_staff_interaction(interaction):
            return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        await apply_punishment(interaction, self.action, self.target_id,
                               str(self.reason.value).strip(), self.duration_minutes)

@bot.tree.command(name="warn", description="Выдать предупреждение игроку")
@app_commands.describe(member="Игрок, которому выносится предупреждение")
async def warn_command(interaction: discord.Interaction, member: discord.Member):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
    await interaction.response.send_modal(PunishReasonModal("warn", member.id))

@bot.tree.command(name="warnings", description="Показать последние наказания игрока")
@app_commands.describe(member="Игрок")
async def warnings_command(interaction: discord.Interaction, member: discord.Member):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
    rows = get_user_punishments(interaction.guild.id, member.id, limit=10)
    if not rows:
        return await interaction.response.send_message(f"У {member.mention} нет наказаний.", ephemeral=True)
    lines = []
    for r in rows:
        dt = time.strftime("%d.%m.%Y %H:%M", time.localtime(r["ts"]))
        line = (f"`{dt}` · {MOD_ACTION_LABELS.get(r['action'], r['action'])} · "
                f"{r['reason'] or 'без причины'} · мод: {r['mod_name'] or '?'}")
        if r["duration"]:
            line += f" · {int(r['duration']) // 60} мин"
        lines.append(line)
    text = f"**Наказания {member.mention} (последние {len(rows)})**\n" + "\n".join(lines)
    await interaction.response.send_message(text[:1950], ephemeral=True)

@bot.tree.command(name="timeout", description="Выдать таймаут игроку")
@app_commands.describe(member="Игрок", minutes="Длительность таймаута в минутах")
async def timeout_command(interaction: discord.Interaction, member: discord.Member,
                          minutes: app_commands.Range[int, 1, 40320]):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
    await interaction.response.send_modal(PunishReasonModal("timeout", member.id, duration_minutes=minutes))

@bot.tree.command(name="untimeout", description="Снять таймаут с игрока")
@app_commands.describe(member="Игрок")
async def untimeout_command(interaction: discord.Interaction, member: discord.Member):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
    try:
        await member.timeout(None, reason=f"Untimeout by {interaction.user}")
    except discord.Forbidden:
        return await interaction.response.send_message("Не удалось снять таймаут: нет прав у бота.", ephemeral=True)
    except discord.HTTPException:
        log.exception("untimeout failed")
        return await interaction.response.send_message("Ошибка Discord API, действие не выполнено.", ephemeral=True)
    add_punishment(interaction.guild.id, member.id, str(member), interaction.user.id,
                   str(interaction.user), "untimeout", "Таймаут снят модератором")
    log_action(interaction.guild.id, "mod_untimeout", actor=interaction.user, target=member)
    await log_mod_action(interaction.guild, "✅ Таймаут снят", discord.Color.green(),
                         f"{member} (`{member.id}`)", interaction.user, "Таймаут снят модератором")
    await interaction.response.send_message(f"✅ Таймаут с {member.mention} снят.", ephemeral=True)

@bot.tree.command(name="kick", description="Кикнуть игрока с сервера")
@app_commands.describe(member="Игрок")
async def kick_command(interaction: discord.Interaction, member: discord.Member):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
    await interaction.response.send_modal(PunishReasonModal("kick", member.id))

@bot.tree.command(name="ban", description="Забанить игрока на сервере")
@app_commands.describe(member="Игрок")
async def ban_command(interaction: discord.Interaction, member: discord.Member):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
    await interaction.response.send_modal(PunishReasonModal("ban", member.id))

@bot.tree.command(name="unban", description="Разбанить пользователя по ID")
@app_commands.describe(user_id="Discord ID пользователя")
async def unban_command(interaction: discord.Interaction, user_id: str):
    if not is_staff_interaction(interaction):
        return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
    user_id = user_id.strip()
    if not (user_id.isascii() and user_id.isdigit()):
        return await interaction.response.send_message("Некорректный ID.", ephemeral=True)
    try:
        await interaction.guild.unban(discord.Object(id=int(user_id)), reason=f"Unban by {interaction.user}")
    except discord.NotFound:
        return await interaction.response.send_message("Этот пользователь не забанен.", ephemeral=True)
    except discord.Forbidden:
        return await interaction.response.send_message("Не удалось разбанить: нет прав у бота.", ephemeral=True)
    target_name = f"ID {user_id}"
    try:
        fetched = await bot.fetch_user(int(user_id))
        target_name = str(fetched)
    except Exception:
        pass
    add_punishment(interaction.guild.id, user_id, target_name, interaction.user.id,
                   str(interaction.user), "unban", "Разбан")
    log_action(interaction.guild.id, "mod_unban", actor=interaction.user, details=target_name)
    await log_mod_action(interaction.guild, "✅ Разбан", discord.Color.green(),
                         f"{target_name} (`{user_id}`)", interaction.user, "Разбан")
    await interaction.response.send_message(f"✅ {target_name} разбанен.", ephemeral=True)

def replace_invite_cache(guild_id, uses_by_code):
    with _db() as conn:
        conn.execute("DELETE FROM invites_cache WHERE guild_id=?", (str(guild_id),))
        for code, uses in uses_by_code.items():
            conn.execute("INSERT INTO invites_cache(guild_id, code, uses) VALUES(?,?,?)",
                         (str(guild_id), str(code), int(uses)))

def get_invite_cache(guild_id):
    with _db() as conn:
        rows = conn.execute("SELECT code, uses FROM invites_cache WHERE guild_id=?",
                            (str(guild_id),)).fetchall()
    return {r["code"]: r["uses"] for r in rows}

def add_invite(guild_id, inviter_id, invited_id, code):
    with _db() as conn:
        conn.execute(
            "INSERT INTO invites(guild_id, inviter_id, invited_id, code, joined_at, left_at) VALUES(?,?,?,?,?,NULL)",
            (str(guild_id), str(inviter_id), str(invited_id), str(code), int(time.time())))

def mark_invite_left(guild_id, invited_id):
    with _db() as conn:
        conn.execute(
            "UPDATE invites SET left_at=? WHERE guild_id=? AND invited_id=? AND left_at IS NULL",
            (int(time.time()), str(guild_id), str(invited_id)))

def invite_stats(guild_id, inviter_id):
    with _db() as conn:
        row = conn.execute(
            "SELECT"
            " SUM(CASE WHEN left_at IS NULL OR left_at - joined_at > ? THEN 1 ELSE 0 END) AS total,"
            " SUM(CASE WHEN left_at IS NULL THEN 1 ELSE 0 END) AS active,"
            " SUM(CASE WHEN left_at IS NOT NULL AND left_at - joined_at > ? THEN 1 ELSE 0 END) AS gone"
            " FROM invites WHERE guild_id=? AND inviter_id=?",
            (INVITE_FAKE_SECONDS, INVITE_FAKE_SECONDS, str(guild_id), str(inviter_id))).fetchone()
    return (row["total"] or 0, row["active"] or 0, row["gone"] or 0)

def count_valid_invites(guild_id, inviter_id):
    total, _, _ = invite_stats(guild_id, inviter_id)
    return total

def top_recruiters(guild_id, limit=10):
    with _db() as conn:
        rows = conn.execute(
            "SELECT inviter_id,"
            " SUM(CASE WHEN left_at IS NULL OR left_at - joined_at > ? THEN 1 ELSE 0 END) AS invites,"
            " SUM(CASE WHEN left_at IS NULL THEN 1 ELSE 0 END) AS active"
            " FROM invites WHERE guild_id=?"
            " GROUP BY inviter_id HAVING invites > 0"
            " ORDER BY active DESC, invites DESC LIMIT ?",
            (INVITE_FAKE_SECONDS, str(guild_id), int(limit))).fetchall()
    return [dict(r) for r in rows]

_invite_perm_warned = False

def _has_invite_perms(guild):
    global _invite_perm_warned
    if guild.me is None or not guild.me.guild_permissions.manage_guild:
        if not _invite_perm_warned:
            log.warning("Учёт инвайтов: у бота нет права Manage Server — отслеживание отключено")
            _invite_perm_warned = True
        return False
    return True

async def collect_invites(guild):
    if not _has_invite_perms(guild):
        return
    try:
        invites = await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Не удалось загрузить инвайты сервера")
        return
    replace_invite_cache(guild.id, {inv.code: (inv.uses or 0) for inv in invites})

async def find_used_invite(guild):
    if not _has_invite_perms(guild):
        return None, None
    try:
        fresh = await guild.invites()
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Не удалось получить инвайты при заходе")
        return None, None
    fresh_map = {inv.code: (inv.uses or 0) for inv in fresh}
    cached = get_invite_cache(guild.id)
    if not cached:
        return None, fresh_map
    used = None
    best_delta = 0
    for inv in fresh:
        delta = (inv.uses or 0) - cached.get(inv.code, 0)
        if delta > best_delta:
            used = inv
            best_delta = delta
    return used, fresh_map

async def award_invite_rewards(guild, inviter):
    total = count_valid_invites(guild.id, inviter.id)
    awarded = []
    for threshold, role_id in INVITE_REWARDS:
        if not role_id or total < threshold:
            continue
        role = guild.get_role(role_id)
        if role is None:
            log.warning("Награда за инвайты: роль %s (порог %s) не найдена", role_id, threshold)
            continue
        if role in inviter.roles:
            continue
        try:
            await inviter.add_roles(role, reason=f"FLCN: {threshold} приглашений")
        except (discord.Forbidden, discord.HTTPException):
            log.exception("Не удалось выдать награду за инвайты")
            continue
        awarded.append(role.name)
        log_action(guild.id, "invite_reward", target=inviter, details=f"{threshold}: {role.name}")
        await send_staff_log(guild, make_log_embed("🏆 Награда за приглашения", discord.Color.gold(),
            [("Кто", f"{inviter} (`{inviter.id}`)"),
             ("Порог", f"{threshold} приглашений (всего {total})"),
             ("Роль", role.mention)]))
    return awarded

async def handle_invite_join(member):
    guild = member.guild
    used, fresh_map = await find_used_invite(guild)
    if fresh_map is None:
        return
    replace_invite_cache(guild.id, fresh_map)
    if used is None or used.inviter is None:
        return
    inviter = used.inviter
    if inviter.id == member.id:
        log_action(guild.id, "invite_self_skipped", target=member, details=used.code)
        return
    add_invite(guild.id, inviter.id, member.id, used.code)
    log_action(guild.id, "invite_join", actor=inviter, target=member, details=used.code)
    await send_staff_log(guild, make_log_embed("📨 Приглашение", discord.Color.green(),
        [("Пригласил", f"{inviter} (`{inviter.id}`)"),
         ("Пришёл", f"{member} (`{member.id}`)"),
         ("Код инвайта", used.code)]))
    inviter_member = guild.get_member(inviter.id)
    if inviter_member is not None:
        await award_invite_rewards(guild, inviter_member)

def _format_member_text(template, member, fallback):
    try:
        return template.format(mention=member.mention, name=member.display_name,
                               username=member.name, server=member.guild.name)
    except (KeyError, IndexError, ValueError):
        return fallback

async def send_welcome(member):
    if not setting_bool("welcome_enabled", True):
        return
    channel_id = _config_id(member.guild, "welcome_channel", WELCOME_CHANNEL_ID)
    if not channel_id:
        return
    channel = member.guild.get_channel(channel_id)
    if channel is None:
        log.warning("welcome_channel=%s не найден", channel_id)
        return
    template = get_setting("welcome_text") or WELCOME_TEXT
    text = _format_member_text(template, member, f"Добро пожаловать, {member.mention}!")
    embed = discord.Embed(description=text, color=discord.Color.green())
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    try:
        await channel.send(content=member.mention, embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Не удалось отправить приветствие")

async def send_goodbye(member):
    if not setting_bool("goodbye_enabled", GOODBYE_ENABLED):
        return
    channel_id = _config_id(member.guild, "goodbye_channel", GOODBYE_CHANNEL_ID)
    if not channel_id:
        return
    channel = member.guild.get_channel(channel_id)
    if channel is None:
        log.warning("goodbye_channel=%s не найден", channel_id)
        return
    template = get_setting("goodbye_text") or GOODBYE_TEXT
    text = _format_member_text(template, member, f"{member.display_name} покинул сервер FALCONS.")
    embed = discord.Embed(description=text, color=discord.Color.dark_gray())
    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Не удалось отправить прощание")

async def push_invites(guild):
    if not API_SECRET_KEY:
        return
    try:
        top = []
        for row in top_recruiters(guild.id, limit=20):
            member = guild.get_member(int(row["inviter_id"]))
            user_name = str(member)[:100] if member else f"ID: {row['inviter_id']}"
            top.append({"user_id": row["inviter_id"], "user_name": user_name,
                        "invites": int(row["invites"]), "active": int(row["active"])})
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(f"{WEBSITE_URL}/api/update-invites",
                              json={"guild_id": str(guild.id), "top": top, "complete": True},
                              headers={"X-Falcons-Secret": API_SECRET_KEY}) as r:
                if r.status != 200:
                    log.error("update-invites HTTP %s", r.status)
    except Exception:
        log.exception("push_invites failed")

@bot.tree.command(name="invites", description="Показать статистику приглашений")
@app_commands.describe(user="Участник (смотреть других может только Ticket Staff)")
async def invites_command(interaction: discord.Interaction, user: discord.Member = None):
    if interaction.guild is None:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    target = user or interaction.user
    if target.id != interaction.user.id and not is_staff_interaction(interaction):
        return await interaction.response.send_message("Смотреть приглашения других может только Ticket Staff.", ephemeral=True)
    total, active, gone = invite_stats(interaction.guild.id, target.id)
    lines = [f"**Приглашения {target.mention}**",
             f"Всего зашло: **{total}**",
             f"Активных: **{active}**",
             f"Ушло: **{gone}**"]
    for threshold, role_id in INVITE_REWARDS:
        if role_id and total < threshold:
            lines.append(f"До награды за {threshold}: ещё **{threshold - total}**")
            break
    await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

@bot.tree.command(name="top_recruiters", description="Топ-10 рекрутеров по активным приглашениям")
async def top_recruiters_command(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    rows = top_recruiters(interaction.guild.id, limit=10)
    if not rows:
        return await interaction.response.send_message("Данных о приглашениях пока нет.", ephemeral=True)
    lines = []
    for i, row in enumerate(rows, 1):
        member = interaction.guild.get_member(int(row["inviter_id"]))
        name = member.mention if member else f"`ID: {row['inviter_id']}`"
        lines.append(f"{i}. {name} — активных: **{row['active']}**, всего: **{row['invites']}**")
    await interaction.response.send_message("**Топ рекрутеров**\n" + "\n".join(lines)[:1900], ephemeral=True)

@bot.tree.command(name="invite_rewards", description="Показать награды за приглашения")
async def invite_rewards_command(interaction: discord.Interaction):
    lines = []
    for threshold, role_id in INVITE_REWARDS:
        if not role_id:
            lines.append(f"**{threshold}** приглашений — не настроено")
            continue
        role = interaction.guild.get_role(role_id) if interaction.guild else None
        lines.append(f"**{threshold}** приглашений — {role.mention if role else 'роль не найдена'}")
    await interaction.response.send_message("**Награды за приглашения**\n" + "\n".join(lines), ephemeral=True)

_spam_tracker = {}

@bot.listen("on_message")
async def antispam_listener(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    if not GUILD_ID or message.guild.id != GUILD_ID:
        return
    if not isinstance(message.author, discord.Member) or has_staff_perms(message.author):
        return
    channel = message.channel
    if isinstance(channel, discord.TextChannel) and channel.topic and "FLCN_TICKET" in channel.topic:
        return
    now = time.monotonic()
    stamps = _spam_tracker.setdefault(message.author.id, [])
    stamps.append(now)
    cutoff = now - SPAM_WINDOW_SECONDS
    while stamps and stamps[0] < cutoff:
        stamps.pop(0)
    if len(stamps) < SPAM_MESSAGE_LIMIT:
        return
    stamps.clear()
    reason = f"Авто-модерация: {SPAM_MESSAGE_LIMIT}+ сообщений за {int(SPAM_WINDOW_SECONDS)} сек"
    try:
        await message.author.timeout(datetime.timedelta(minutes=SPAM_TIMEOUT_MINUTES), reason=reason)
    except (discord.Forbidden, discord.HTTPException):
        log.exception("Антиспам: не удалось выдать таймаут")
        return
    duration = SPAM_TIMEOUT_MINUTES * 60
    add_punishment(message.guild.id, message.author.id, str(message.author), bot.user.id,
                   str(bot.user), "timeout", reason, duration, int(time.time()) + duration)
    log_action(message.guild.id, "antispam_timeout", target=message.author, details=reason)
    await log_mod_action(message.guild, "⏳ Антиспам: таймаут", discord.Color.orange(),
                         f"{message.author} (`{message.author.id}`)", bot.user, reason,
                         f"{SPAM_TIMEOUT_MINUTES} мин")

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN не задан в .env")
    init_db()
    bot.run(TOKEN, log_handler=None)
