import os
import json
import re
import asyncio
import logging
import sqlite3
import time
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

init_db()
