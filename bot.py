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
UPDATE_SECONDS = max(300, int(os.getenv("UPDATE_SECONDS", "600") or 600))
WEBSITE_URL = os.getenv("WEBSITE_URL", "https://falcons-panel.onrender.com").strip().rstrip("/")
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "").strip()
BOT_HTTP_PORT = int(os.getenv("BOT_HTTP_PORT", os.getenv("SERVER_PORT", "8080")) or 8080)
BOT_HTTP_HOST = os.getenv("BOT_HTTP_HOST", "0.0.0.0").strip()

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
            reason TEXT
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
        """)

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
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
        else:
            await self.tree.sync()
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
    for r in member.roles:
        if TICKET_STAFF_ROLE_ID and r.id == TICKET_STAFF_ROLE_ID:
            return True
        if DEP_LEADER_ROLE_ID and r.id == DEP_LEADER_ROLE_ID:
            return True
        if r.name.casefold() == "ticket staff":
            return True
    return False

def get_staff_role(guild):
    role = guild.get_role(TICKET_STAFF_ROLE_ID) if TICKET_STAFF_ROLE_ID else None
    if role is None:
        role = discord.utils.find(lambda r: "ticket" in r.name.casefold() and "staff" in r.name.casefold(), guild.roles)
    return role

def is_staff_interaction(interaction):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    return has_staff_perms(interaction.user)

async def send_staff_log(guild, embed):
    if not STAFF_LOG_CHANNEL_ID:
        return
    ch = guild.get_channel(STAFF_LOG_CHANNEL_ID)
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

async def push_stats(guild, total, online, clan):
    if not API_SECRET_KEY:
        return
    try:
        joins_7d = get_joins_last_7_days(guild.id)
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(f"{WEBSITE_URL}/api/update-stats",
                              json={"total": total, "online": online, "clan": clan,
                                    "joins_7d": joins_7d},
                              headers={"X-Falcons-Secret": API_SECRET_KEY}) as r:
                if r.status != 200:
                    log.error("update-stats HTTP %s", r.status)
    except Exception:
        log.exception("push_stats failed")

async def push_staff(guild):
    if not API_SECRET_KEY or not TICKET_STAFF_ROLE_ID:
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
        if GUILD_ID and guild.id != GUILD_ID:
            return
        try:
            await guild.chunk(cache=True)
        except Exception:
            log.exception("chunk failed")
        members = list(guild.members)
        total = guild.member_count if guild.member_count is not None else len(members)
        online = sum(1 for m in members if m.status not in (discord.Status.offline, discord.Status.invisible))
        clan_role = guild.get_role(CLAN_ROLE_ID) if CLAN_ROLE_ID else None
        clan = sum(1 for m in members if clan_role and clan_role in m.roles)
        await push_stats(guild, total, online, clan)
        await push_staff(guild)
        await push_tickets(guild)
        try:
            category = await get_category(guild)
            for key, count in (("members", total), ("online", online), ("clan", clan)):
                await update_counter(guild, category, key, count)
                await asyncio.sleep(1.2)
        except Exception:
            log.exception("counters failed")

@tasks.loop(seconds=UPDATE_SECONDS)
async def updater():
    for guild in bot.guilds:
        if not GUILD_ID or guild.id == GUILD_ID:
            try:
                await refresh(guild)
            except Exception:
                log.exception("auto refresh failed")

@updater.before_loop
async def before_updater():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    log.info("Connected as %s (%s)", bot.user, bot.user.id)
    if not updater.is_running():
        updater.start()
    for guild in bot.guilds:
        if not GUILD_ID or guild.id == GUILD_ID:
            try:
                await refresh(guild)
            except Exception:
                log.exception("initial refresh failed")

@bot.event
async def on_member_join(member):
    if not GUILD_ID or member.guild.id == GUILD_ID:
        try:
            with _db() as conn:
                conn.execute("INSERT INTO joins(guild_id, user_id, ts) VALUES(?,?,?)",
                             (str(member.guild.id), str(member.id), int(time.time())))
        except Exception:
            log.exception("Не удалось записать join")
        try:
            await refresh(member.guild)
        except Exception:
            pass

@bot.event
async def on_member_remove(member):
    if not GUILD_ID or member.guild.id == GUILD_ID:
        try:
            await refresh(member.guild)
        except Exception:
            pass
            
