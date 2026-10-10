import os
import json
import re
import asyncio
import logging
from urllib.parse import urlparse

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(ENV_PATH, override=True)

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
CLAN_ROLE_ID = int(os.getenv("CLAN_ROLE_ID", "0") or 0)
CATEGORY_NAME = os.getenv("STATS_CATEGORY_NAME", "▪・ꜱᴇʀᴠᴇʀ ꜱᴛᴀᴛꜱ").strip()
UPDATE_SECONDS = max(300, int(os.getenv("UPDATE_SECONDS", "600") or 600))
TICKET_STAFF_ROLE_ID = 1507860009630040245
TICKET_CATEGORY_NAME = os.getenv("TICKET_CATEGORY_NAME", "FLCN TICKETS").strip()
QUESTIONS_FILE = str(Path(__file__).resolve().parent / "ticket_questions.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("falcons")
log.info("Loaded config: guild_id=%s, ticket_staff_role_id=%s, env_path=%s", GUILD_ID, TICKET_STAFF_ROLE_ID, ENV_PATH)

NAMES = {
    "members": "👥・ᴍᴇᴍʙᴇʀs",
    "online": "🟢・ᴏɴʟɪɴᴇ",
    "clan": "🛡️・ᴄʟᴀɴ",
}

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


TICKET_COUNTER_FILE = str(Path(__file__).resolve().parent / "ticket_counter.json")

def next_ticket_number(guild_id: int) -> int:
    """Persist a separate sequential ticket number for each Discord server."""
    try:
        with open(TICKET_COUNTER_FILE, "r", encoding="utf-8") as f:
            counters = json.load(f)
        if not isinstance(counters, dict):
            counters = {}
    except (OSError, json.JSONDecodeError):
        counters = {}
    key = str(guild_id)
    number = int(counters.get(key, 0)) + 1
    counters[key] = number
    with open(TICKET_COUNTER_FILE, "w", encoding="utf-8") as f:
        json.dump(counters, f, ensure_ascii=False, indent=2)
    return number

def load_questions(category="clan"):
    try:
        with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get(category), list) and data[category]:
            return [str(x).strip() for x in data[category] if str(x).strip()]
    except (OSError, json.JSONDecodeError):
        pass
    return CATEGORY_QUESTIONS.get(category, CATEGORY_QUESTIONS["other"]).copy()

def save_questions(category, questions):
    try:
        with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[category] = questions
    with open(QUESTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

class FalconsBot(commands.Bot):
    async def setup_hook(self):
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild_obj)
            await self.tree.sync(guild=guild_obj)
        else:
            await self.tree.sync()

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.presences = True
intents.message_content = True  # Required to detect BattleMetrics links in ticket messages.
bot = FalconsBot(command_prefix="!", intents=intents)

async def get_category(guild):
    wanted = CATEGORY_NAME.casefold()
    category = next((c for c in guild.categories if c.name.casefold() == wanted), None)
    if category:
        return category
    prefixes = tuple(NAMES.values())
    for existing in guild.categories:
        if any(ch.name.startswith(prefix) for ch in existing.voice_channels for prefix in prefixes):
            log.info("Reusing existing stats category: %s", existing.name)
            return existing
    return await guild.create_category(CATEGORY_NAME, reason="FLCN stats")

async def update_counter(guild, category, key, count):
    prefix = NAMES[key]
    channel = discord.utils.find(
        lambda c: c.name == prefix or c.name.startswith(prefix + " :") or c.name.startswith(prefix + ":"),
        guild.voice_channels,
    )
    target = f"{prefix} : {count}"
    if channel is None:
        overwrites = {guild.default_role: discord.PermissionOverwrite(connect=False, view_channel=True)}
        channel = await guild.create_voice_channel(target, category=category, overwrites=overwrites, reason="FLCN stats")
    elif channel.name != target:
        await channel.edit(name=target, reason="FLCN stats refresh")
    if channel.category_id != category.id:
        await channel.edit(category=category, reason="Organize FLCN stats")

refresh_lock = asyncio.Lock()

async def refresh(guild):
    async with refresh_lock:
        if GUILD_ID and guild.id != GUILD_ID:
            return
        try:
            await guild.chunk(cache=True)
        except Exception:
            log.exception("Could not chunk guild members")
        members = list(guild.members)
        total = guild.member_count if guild.member_count is not None else len(members)
        online = sum(1 for m in members if m.status not in (discord.Status.offline, discord.Status.invisible))
        clan_role = guild.get_role(CLAN_ROLE_ID) if CLAN_ROLE_ID else None
        clan = sum(1 for m in members if clan_role and clan_role in m.roles)
        category = await get_category(guild)
        for key, count in (("members", total), ("online", online), ("clan", clan)):
            await update_counter(guild, category, key, count)
            await asyncio.sleep(1.2)

@tasks.loop(seconds=UPDATE_SECONDS)
async def updater():
    for guild in bot.guilds:
        if not GUILD_ID or guild.id == GUILD_ID:
            try:
                await refresh(guild)
            except Exception:
                log.exception("Stats refresh failed for guild %s", guild.id)

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
                log.exception("Initial stats refresh failed")

@bot.event
async def on_member_join(member):
    if not GUILD_ID or member.guild.id == GUILD_ID:
        try:
            await refresh(member.guild)
        except Exception:
            log.exception("Join refresh failed")

@bot.event
async def on_member_remove(member):
    if not GUILD_ID or member.guild.id == GUILD_ID:
        try:
            await refresh(member.guild)
        except Exception:
            log.exception("Leave refresh failed")

def get_staff_role(guild):
    """Use the configured role ID, or fall back to the exact role name."""
    role = guild.get_role(TICKET_STAFF_ROLE_ID) if TICKET_STAFF_ROLE_ID else None
    if role is None:
        # Fallback only for servers where the configured role ID is unavailable.
        role = discord.utils.find(lambda r: "ticket" in r.name.casefold() and "staff" in r.name.casefold(), guild.roles)
    return role

def is_staff(interaction):
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return False
    member = interaction.user
    return bool(
        member.guild_permissions.administrator
        or member.guild_permissions.manage_guild
        or any(
            (TICKET_STAFF_ROLE_ID and r.id == TICKET_STAFF_ROLE_ID)
            or r.name.casefold() == "ticket staff"
            for r in member.roles
        )
    )

def safe_channel_name(name):
    cleaned = re.sub(r"[^a-z0-9-]", "-", name.lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return (cleaned or "player")[:18]

async def get_ticket_category(guild):
    """Ensure ticket category exists and grants staff/bot access."""
    cat = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
    staff_role = get_staff_role(guild)
    bot_member = guild.me
    required = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }
    if staff_role:
        required[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True, manage_messages=True,
        )
    if bot_member:
        required[bot_member] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True,
            attach_files=True, embed_links=True, manage_channels=True, manage_messages=True,
        )
    if cat is None:
        return await guild.create_category(
            TICKET_CATEGORY_NAME, overwrites=required, reason="FLCN ticket setup"
        )
    # Preserve unrelated category overwrites and only PATCH when required permissions differ.
    current = dict(cat.overwrites)
    changed = False
    for target, overwrite in required.items():
        if current.get(target) != overwrite:
            current[target] = overwrite
            changed = True
    if changed:
        await cat.edit(overwrites=current, reason="Repair FLCN ticket category permissions")
    return cat

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
            attach_files=True, embed_links=True, manage_messages=True,
        )
    ticket_number = next_ticket_number(guild.id)
    channel_name = f"{ticket_number:03d}・{safe_channel_name(interaction.user.name)}"
    channel = await guild.create_text_channel(
        channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"FLCN_TICKET|type={category_key}|number={ticket_number}|owner={interaction.user.id}",
        reason=f"FLCN ticket #{ticket_number}"
    )
    embed = discord.Embed(
        title=f"Тикет №{ticket_number} — {CATEGORY_LABELS.get(category_key, 'Обращение')}",
        color=discord.Color.blurple()
    )
    embed.add_field(name="Номер тикета", value=f"`#{ticket_number}`", inline=True)
    embed.add_field(name="Категория", value=CATEGORY_LABELS.get(category_key, "Обращение"), inline=True)
    embed.add_field(name="Открыл", value=f"{interaction.user.mention} (`{interaction.user.display_name}`)", inline=False)
    embed.add_field(name="Discord username", value=f"`{interaction.user.name}`", inline=True)
    embed.add_field(name="Discord ID", value=f"`{interaction.user.id}`", inline=True)
    for question, answer in answers:
        # Keep within Discord embed field limits.
        answer = answer.strip() or "—"
        if len(answer) > 1024:
            answer = answer[:1018] + " […]"
        label = question[:256]
        if len(embed.fields) < 25:
            embed.add_field(name=label, value=answer, inline=False)
    if category_key == "clan":
        embed.set_footer(text="Для набора приложи скрин Steam и ссылку BattleMetrics.")
    await channel.send(content=staff_role.mention if staff_role else None, embed=embed, view=CloseTicketView())
    # Only clan applications need Steam screenshots and BattleMetrics.
    if category_key == "clan":
        await channel.send(
            f"{interaction.user.mention}, тикет №{ticket_number} создан. Прикрепи сюда скрин Steam и отправь ссылку BattleMetrics, если её ещё нет в ответах."
        )
    else:
        await channel.send(f"{interaction.user.mention}, тикет №{ticket_number} создан. Опиши обращение здесь; Ticket Staff ответят в этом канале.")
    await interaction.followup.send(f"Готово! Твоя заявка создана: {channel.mention}", ephemeral=True)

class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Закрыть тикет", style=discord.ButtonStyle.danger, custom_id="flcn:close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_staff(interaction):
            return await interaction.response.send_message("Закрывать тикеты может только Ticket Staff или администратор.", ephemeral=True)
        await interaction.response.send_message("Закрываю тикет…", ephemeral=True)
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")

class ApplicationForm:
    """The modal sequence is created dynamically in start_application()."""
    pass

async def start_application(interaction: discord.Interaction, category_key: str):
    questions = load_questions(category_key)
    if not questions:
        return await interaction.response.send_message("Анкета пока не настроена.", ephemeral=True)
    answers = []
    # Discord allows up to five inputs in each modal, so split unlimited questions into batches.
    async def show_batch(inter, start):
        batch = questions[start:start + 5]
        class BatchModal(discord.ui.Modal, title=f"Заявка ({start + 1}–{start + len(batch)})"):
            def __init__(self):
                super().__init__(timeout=600)
                self.inputs = []
                for offset, question in enumerate(batch):
                    field = discord.ui.TextInput(
                        label=question[:45],
                        placeholder="Напиши ответ…"[:100],
                        style=discord.TextStyle.paragraph,
                        required=True,
                        min_length=1,
                        max_length=1000,
                    )
                    self.inputs.append((question, field))
                    self.add_item(field)
            async def on_submit(self, modal_interaction):
                answers.extend((q, str(field.value)) for q, field in self.inputs)
                next_start = start + len(batch)
                if next_start < len(questions):
                    await show_batch(modal_interaction, next_start)
                else:
                    await modal_interaction.response.defer(ephemeral=True, thinking=True)
                    await create_ticket(modal_interaction, category_key, answers)
        await inter.response.send_modal(BatchModal())
    await show_batch(interaction, 0)

class TicketCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Набор в клан", value="clan", description="Анкета и проверка игрока", emoji="⚔️"),
            discord.SelectOption(label="Партнёрство", value="partner", description="Реклама и сотрудничество", emoji="🤝"),
            discord.SelectOption(label="Вопрос по клану", value="question", description="Вопрос руководству", emoji="💬"),
            discord.SelectOption(label="Жалоба на игрока", value="report", description="Сообщить о нарушении", emoji="🛡️"),
            discord.SelectOption(label="Другое обращение", value="other", description="Другие вопросы", emoji="🔧"),
        ]
        super().__init__(placeholder="Выбери категорию обращения…", min_values=1, max_values=1, options=options, custom_id="flcn:category_select")

    async def callback(self, interaction: discord.Interaction):
        await start_application(interaction, self.values[0])

class TicketPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(TicketCategorySelect())

@bot.tree.command(name="falcons_stats", description="Обновить счётчики сервера")
async def falcons_stats(interaction: discord.Interaction):
    if GUILD_ID and interaction.guild_id != GUILD_ID:
        return await interaction.response.send_message("Эта команда предназначена только для настроенного сервера.", ephemeral=True)
    if interaction.guild is None:
        return await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("Нужно право «Управлять сервером».", ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        await refresh(interaction.guild)
        await interaction.followup.send("Счётчики обновлены.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("Не хватает прав. Проверь права бота и Gateway Intents.", ephemeral=True)
    except Exception:
        log.exception("Manual refresh failed")
        await interaction.followup.send("Ошибка обновления. Проверь окно запуска.", ephemeral=True)

@bot.tree.command(name="setup_tickets", description="Создать панель заявок FLCN")
async def setup_tickets(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
    if not is_staff(interaction):
        return await interaction.response.send_message("Нужно право «Управлять сервером» или роль Ticket Staff.", ephemeral=True)
    embed = discord.Embed(
        title="FALCONS SUPPORT",
        description=(
            "**Выбери категорию обращения в меню ниже.**\n\n"
            "⚔️ **Набор в клан** — от 15 лет и от 3000 часов в Rust.\n"
            "Скриншот Steam понадобится только для заявки в клан — его можно прикрепить внутри тикета."
        ),
        color=discord.Color.blurple()
    )
    await interaction.channel.send(embed=embed, view=TicketPanelView())
    await interaction.response.send_message("Панель заявок опубликована.", ephemeral=True)

@bot.tree.command(name="ticket_add_question", description="Добавить вопрос в анкету категории")
@discord.app_commands.describe(category="clan / partner / question / report / other", question="Текст нового вопроса")
async def ticket_add_question(interaction: discord.Interaction, category: str, question: str):
    if not is_staff(interaction):
        return await interaction.response.send_message("Нужно право «Управлять сервером» или роль Ticket Staff.", ephemeral=True)
    if category not in CATEGORY_QUESTIONS:
        return await interaction.response.send_message("Категория: clan, partner, question, report или other.", ephemeral=True)
    questions = load_questions(category)
    questions.append(question.strip())
    save_questions(category, questions)
    await interaction.response.send_message(f"Добавил вопрос в «{CATEGORY_LABELS[category]}». Всего: {len(questions)}.", ephemeral=True)

@bot.tree.command(name="ticket_remove_question", description="Удалить вопрос в категории")
@discord.app_commands.describe(category="clan / partner / question / report / other", number="Номер вопроса")
async def ticket_remove_question(interaction: discord.Interaction, category: str, number: int):
    if not is_staff(interaction):
        return await interaction.response.send_message("Нужно право «Управлять сервером» или роль Ticket Staff.", ephemeral=True)
    if category not in CATEGORY_QUESTIONS:
        return await interaction.response.send_message("Категория: clan, partner, question, report или other.", ephemeral=True)
    questions = load_questions(category)
    if number < 1 or number > len(questions):
        return await interaction.response.send_message(f"Номер должен быть от 1 до {len(questions)}.", ephemeral=True)
    removed = questions.pop(number - 1)
    save_questions(category, questions)
    await interaction.response.send_message(f"Удалил вопрос: {removed}", ephemeral=True)

@bot.tree.command(name="ticket_questions", description="Показать вопросы категории")
@discord.app_commands.describe(category="clan / partner / question / report / other")
async def ticket_questions(interaction: discord.Interaction, category: str):
    if not is_staff(interaction):
        return await interaction.response.send_message("Нужно право «Управлять сервером» или роль Ticket Staff.", ephemeral=True)
    if category not in CATEGORY_QUESTIONS:
        return await interaction.response.send_message("Категория: clan, partner, question, report или other.", ephemeral=True)
    questions = load_questions(category)
    body = "\n".join(f"{i}. {q}" for i, q in enumerate(questions, 1)) or "Вопросов пока нет."
    await interaction.response.send_message(f"**{CATEGORY_LABELS[category]}**\n{body}"[:1950], ephemeral=True)

@bot.tree.command(name="ticket_debug", description="Проверить настройку доступа к тикетам")
async def ticket_debug(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("Нужно право «Управлять сервером».", ephemeral=True)
    guild = interaction.guild
    role = get_staff_role(guild)
    category = discord.utils.get(guild.categories, name=TICKET_CATEGORY_NAME)
    lines = [
        f"TICKET_STAFF_ROLE_ID в настройке: `{TICKET_STAFF_ROLE_ID}`",
        f"Роль найдена на сервере: {'да — ' + role.name if role else 'НЕТ — ID неверный или не задан'}",
        f"Категория тикетов найдена: {'да' if category else 'нет'}",
        f"Бот имеет Manage Channels: {'да' if guild.me and guild.me.guild_permissions.manage_channels else 'нет'}",
        f"Staff видит категорию: {'да' if category and role and category.overwrites_for(role).view_channel is True else 'проверь'}",
    ]
    if role:
        lines.append(f"Роль Ticket Staff: {role.mention} (ID `{role.id}`)")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="ticket_repair_permissions", description="Исправить права Ticket Staff в существующих тикетах")
async def ticket_repair_permissions(interaction: discord.Interaction):
    if interaction.guild is None:
        return await interaction.response.send_message("Команда работает только на сервере.", ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.response.send_message("Нужно право «Управлять сервером».", ephemeral=True)
    role = get_staff_role(interaction.guild)
    if role is None:
        return await interaction.response.send_message(
            "Не нашёл роль Ticket Staff. Проверь ID в .env и название роли.", ephemeral=True
        )
    category = await get_ticket_category(interaction.guild)
    repaired = 0
    for channel in category.text_channels:
        try:
            await channel.set_permissions(
                role, view_channel=True, send_messages=True, read_message_history=True,
                attach_files=True, embed_links=True, manage_messages=True,
                reason="Repair FLCN Ticket Staff permissions",
            )
            repaired += 1
        except discord.Forbidden:
            log.exception("Cannot repair permissions for ticket channel %s", channel.id)
    await interaction.response.send_message(
        f"Роль {role.mention} найдена. Доступ проверен; права обновлены в {repaired} тикетах.",
        ephemeral=True,
    )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    channel = message.channel
    # Link recognition is restricted to clan-application tickets.
    is_clan_ticket = (
        isinstance(channel, discord.TextChannel)
        and bool(channel.topic)
        and "FLCN_TICKET|type=clan|" in channel.topic
    )
    if is_clan_ticket:
        bm_match = re.search(
            r"https?://(?:www\.)?battlemetrics\.com/players/(\d+)(?:[^\s]*)?",
            message.content,
            re.IGNORECASE,
        )
        steam_match = re.search(
            r"https?://(?:www\.)?steamcommunity\.com/(?:profiles/\d+|id/[^/\s?#]+)(?:[^\s]*)?",
            message.content,
            re.IGNORECASE,
        )

        replies = []
        if steam_match:
            steam_url = steam_match.group(0).rstrip(".,)>]}")
            replies.append(
                f"Нашёл Steam-профиль: {steam_url}\n"
                "Ссылка распознана. Убедись, что профиль доступен для просмотра."
            )
        if bm_match:
            player_id = bm_match.group(1)
            replies.append(
                f"Нашёл BattleMetrics ID `{player_id}`: https://www.battlemetrics.com/players/{player_id}\n"
                "Ссылка распознана. Автоматические часы и статистика пока не подключены — Ticket Staff должны сверить профиль."
            )
        if replies:
            await message.reply("\n\n".join(replies), mention_author=False)
    await bot.process_commands(message)

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Заполни DISCORD_TOKEN в файле .env")
    bot.add_view(TicketPanelView())
    bot.add_view(CloseTicketView())
    bot.run(TOKEN, log_handler=None)
