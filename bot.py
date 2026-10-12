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

    view = TicketActionsView() if category_key == "clan" else None
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
        await interaction.channel.delete(reason=f"Closed by {interaction.user}")
        asyncio.create_task(push_tickets(interaction.guild))

class TicketActionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Принять", style=discord.ButtonStyle.success, custom_id="flcn:accept")
    async def accept(self, interaction, button):
        if not is_staff_interaction(interaction):
            return await interaction.response.send_message("Недостаточно прав.", ephemeral=True)
        t = get_ticket_by_channel(interaction.channel.id)
        if not t or t["status"] != "open":
            return await interaction.response.send_message("Эта заявка уже обработана.", ephemeral=True)
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

@bot.tree.command(name="falcons_stats", description="Обновить счётчики сервера")
async def falcons_stats(interaction):
    if GUILD_ID and interaction.guild_id != GUILD_ID:
        return await interaction.response.send_message("Только для настроенного сервера.", ephemeral=True)
    if interaction.guild is None:
        return await interaction.response.send_message("Только на сервере.", ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("Нужно право «Управлять сервером».", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await refresh(interaction.guild)
        await interaction.followup.send("Счётчики, staff и тикеты обновлены.", ephemeral=True)
    except Exception:
        log.exception("Manual refresh failed")
        await interaction.followup.send("Ошибка. Смотри логи.", ephemeral=True)

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
    embed.set_image(url="https://files.catbox.moe/nksitu.png")
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("Панель заявок опубликована.", ephemeral=True)

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
            replies.append(f"Нашёл BattleMetrics ID `{bm_match.group(1)}`.")
        if replies:
            await message.reply("\n\n".join(replies), mention_author=False)
    await bot.process_commands(message)

def _check_secret(request):
    if not API_SECRET_KEY:
        return False
    return request.headers.get("X-Falcons-Secret", "") == API_SECRET_KEY

def _get_guild_and_actor(data):
    guild = bot.get_guild(GUILD_ID) if GUILD_ID else (bot.guilds[0] if bot.guilds else None)
    if guild is None:
        return None, None, None
    actor = guild.get_member(int(data.get("actor_id", 0)))
    channel = guild.get_channel(int(data.get("channel_id", 0)))
    return guild, actor, channel

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
        await asyncio.sleep(2)
        await channel.delete(reason="Rejected from site")
    except Exception:
        pass
    asyncio.create_task(push_tickets(guild))
    return aiohttp_web.json_response({"ok": True})

async def start_bot_http():
    app = aiohttp_web.Application()
    app.router.add_post("/bot/ticket/close", http_close_ticket)
    app.router.add_post("/bot/ticket/accept", http_accept_ticket)
    app.router.add_post("/bot/ticket/reject", http_reject_ticket)
    runner = aiohttp_web.AppRunner(app)
    await runner.setup()
    site = aiohttp_web.TCPSite(runner, BOT_HTTP_HOST, BOT_HTTP_PORT)
    await site.start()
    log.info("Bot HTTP API запущен на %s:%s", BOT_HTTP_HOST, BOT_HTTP_PORT)

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN не задан в .env")
    init_db()
    bot.run(TOKEN, log_handler=None)
