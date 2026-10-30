import asyncio
import html
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, BusinessConnection,
)

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
CHANNEL = os.getenv("CHANNEL", "@linksfromerebus")
GROUP = os.getenv("GROUP", "@ecochamber")
DB_PATH = Path(os.getenv("DB_PATH", "bot_data.sqlite3"))
FREE_DAILY_LIMIT = int(os.getenv("FREE_DAILY_LIMIT", "20"))  # default for every free user
SAVE_DIR = Path("saved_media")
SAVE_DIR.mkdir(exist_ok=True)
TRIGGER_WORD = "shit"

if not BOT_TOKEN or not OWNER_ID:
    raise RuntimeError("Set BOT_TOKEN and OWNER_ID in your .env file")

logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    force=True,
)
logging.getLogger("aiogram").setLevel(logging.INFO)

bot = Bot(BOT_TOKEN)
dp = Dispatcher()


# ----------------------------------------------------------------------------
# Keyboards
# ----------------------------------------------------------------------------
def media_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍💻 Developer", url="https://t.me/Noxmirage")],
        [InlineKeyboardButton(text="📢 Channel", url="https://t.me/HazenNexus")],
        [InlineKeyboardButton(text="🔗 Erebus Redirect", url="https://t.me/Erebusredirect")],
    ])


def home_keyboard(is_admin=False):
    rows = [
        [InlineKeyboardButton(text="📸 How to Archive", callback_data="howto"),
         InlineKeyboardButton(text="⭐ My Premium", callback_data="mypremium")],
        [InlineKeyboardButton(text="📊 My Statistics", callback_data="mystats"),
         InlineKeyboardButton(text="🔄 Check Membership", callback_data="checkjoin")],
        [InlineKeyboardButton(text="📢 Updates", url="https://t.me/linksfromerebus")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 Admin Panel", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def join_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Join Channel", url="https://t.me/linksfromerebus")],
        [InlineKeyboardButton(text="👥 Join Group", url="https://t.me/ecochamber")],
        [InlineKeyboardButton(text="✅ I've Joined", callback_data="checkjoin")],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistics", callback_data="astats"),
         InlineKeyboardButton(text="👥 Users", callback_data="ausers")],
        [InlineKeyboardButton(text="⭐ Premium Users", callback_data="apremium"),
         InlineKeyboardButton(text="📢 Broadcast", callback_data="abroadcast")],
        [InlineKeyboardButton(text="🔎 User Info", callback_data="auserinfo"),
         InlineKeyboardButton(text="🚫 Ban / Unban", callback_data="aban")],
        [InlineKeyboardButton(text="⬅️ Main Menu", callback_data="home")],
    ])


# ----------------------------------------------------------------------------
# Database
#
# users                 one row per person who started the bot
#                       (custom_limit = per-user daily limit, NULL = default)
# premium               premium expiry per user
# business_connections  one row per connected Business account (owner_id = who owns it)
# usage_daily           per-user, per-day counter  <- this is what the limit checks
# archives              history of every archived file
# processed_messages    stops the same trigger being handled twice
# broadcasts            admin broadcast history
# All timestamps are UTC ISO strings; "day" is a UTC YYYY-MM-DD string.
# ----------------------------------------------------------------------------
@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            banned INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS premium (
            user_id INTEGER PRIMARY KEY,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS business_connections (
            connection_id TEXT PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            user_chat_id INTEGER NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS usage_daily (
            user_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, day)
        );
        CREATE TABLE IF NOT EXISTS archives (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            source_chat_id INTEGER NOT NULL,
            source_message_id INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            file_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS processed_messages (
            connection_id TEXT NOT NULL,
            source_chat_id INTEGER NOT NULL,
            command_message_id INTEGER NOT NULL,
            processed_at TEXT NOT NULL,
            PRIMARY KEY(connection_id, source_chat_id, command_message_id)
        );
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            sent_count INTEGER NOT NULL,
            failed_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_archives_user ON archives(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_conn_owner ON business_connections(owner_id);
        """)

        # Migration: per-user limit column for databases created by older versions.
        cols = [r["name"] for r in con.execute("PRAGMA table_info(users)")]
        if "custom_limit" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN custom_limit INTEGER")

        # Migration: build today's/old counters from the existing archive history.
        con.execute("""
            INSERT OR IGNORE INTO usage_daily(user_id, day, count)
            SELECT user_id, substr(created_at, 1, 10), COUNT(*)
            FROM archives GROUP BY user_id, substr(created_at, 1, 10)
        """)


def now():
    return datetime.now(timezone.utc)


def today():
    return now().strftime("%Y-%m-%d")


# ---- users ------------------------------------------------------------------
def upsert_user(user):
    stamp = now().isoformat()
    with db() as con:
        con.execute("""
            INSERT INTO users(user_id, username, first_name, joined_at, last_seen)
            VALUES(?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              username=excluded.username,
              first_name=excluded.first_name,
              last_seen=excluded.last_seen
        """, (user.id, user.username, user.first_name, stamp, stamp))


def get_user(user_id):
    with db() as con:
        return con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def is_banned(user_id):
    row = get_user(user_id)
    return bool(row and row["banned"])


def find_registered_user(username):
    # Bots cannot resolve arbitrary usernames; this only finds users who started the bot.
    username = username.lstrip("@").lower()
    with db() as con:
        return con.execute("SELECT * FROM users WHERE lower(username)=?", (username,)).fetchone()


# ---- premium & limits (everything here is per user) ------------------------------
def premium_expiry(user_id):
    with db() as con:
        row = con.execute("SELECT expires_at FROM premium WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return None
    try:
        expiry = datetime.fromisoformat(row["expires_at"])
        return expiry if expiry > now() else None
    except ValueError:
        return None


def is_premium(user_id):
    return premium_expiry(user_id) is not None


def user_daily_limit(user_id):
    """Daily limit for THIS user. None = unlimited (premium)."""
    if is_premium(user_id):
        return None
    row = get_user(user_id)
    if row and row["custom_limit"] is not None:
        return row["custom_limit"]
    return FREE_DAILY_LIMIT


def daily_count(user_id):
    with db() as con:
        row = con.execute(
            "SELECT count FROM usage_daily WHERE user_id=? AND day=?", (user_id, today())
        ).fetchone()
    return row["count"] if row else 0


def has_quota(user_id):
    limit = user_daily_limit(user_id)
    return limit is None or daily_count(user_id) < limit


# ---- business connections -------------------------------------------------------
def save_connection(connection: BusinessConnection):
    with db() as con:
        con.execute("""
            INSERT INTO business_connections(connection_id, owner_id, user_chat_id, is_enabled)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(connection_id) DO UPDATE SET
                owner_id = excluded.owner_id,
                user_chat_id = excluded.user_chat_id,
                is_enabled = excluded.is_enabled
        """, (connection.id, connection.user.id, connection.user_chat_id,
              1 if connection.is_enabled else 0))
    logging.info("Saved Business connection: %s (owner %s)", connection.id, connection.user.id)


def get_connection(connection_id):
    with db() as con:
        row = con.execute(
            "SELECT * FROM business_connections WHERE connection_id = ?", (connection_id,)
        ).fetchone()
    if row and row["is_enabled"] == 1:
        return row
    return None


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
async def safe_edit(callback: CallbackQuery, text, reply_markup=None, parse_mode=None):
    """edit_text that ignores Telegram's 'message is not modified' error."""
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


async def membership_ok(user_id):
    # The bot must be an administrator in both chats to reliably check membership.
    for chat in (CHANNEL, GROUP):
        try:
            member = await bot.get_chat_member(chat_id=chat, user_id=user_id)
            if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
                return False
        except Exception:
            logging.exception("Membership check failed for %s", chat)
            return False
    return True


class AdminFlow(StatesGroup):
    broadcast_text = State()


# ----------------------------------------------------------------------------
# Business connection updates
# ----------------------------------------------------------------------------
@dp.business_connection()
async def on_business_connection(connection: BusinessConnection):
    logging.info("BUSINESS CONNECTION id=%s owner=%s enabled=%s user_chat=%s",
                 connection.id, connection.user.id, connection.is_enabled, connection.user_chat_id)
    try:
        save_connection(connection)
    except Exception:
        logging.exception("FAILED TO SAVE BUSINESS CONNECTION")


# ----------------------------------------------------------------------------
# User commands / menus
# ----------------------------------------------------------------------------
@dp.message(CommandStart())
async def start(message: Message):
    if not message.from_user:
        return
    upsert_user(message.from_user)
    if is_banned(message.from_user.id):
        await message.answer("🚫 Your access has been disabled.")
        return
    if not await membership_ok(message.from_user.id):
        await message.answer(
            "👋 Welcome! Join our channel and group to unlock the bot.",
            reply_markup=join_keyboard()
        )
        return
    await message.answer(
        "👋 Welcome to the Media Archive Bot!\n\n"
        "Connect this bot to your Telegram Business account, then reply "
        f"to a photo/video/GIF/document/video note with {TRIGGER_WORD} "
        "from your connected account.",
        reply_markup=home_keyboard(message.from_user.id == OWNER_ID)
    )


@dp.callback_query(F.data == "checkjoin")
async def check_join(callback: CallbackQuery):
    if not callback.from_user:
        return
    upsert_user(callback.from_user)
    if is_banned(callback.from_user.id):
        await callback.answer("Access disabled.", show_alert=True)
        return
    if await membership_ok(callback.from_user.id):
        await safe_edit(callback, "✅ Membership verified. You can use the bot now.",
                        home_keyboard(callback.from_user.id == OWNER_ID))
        await callback.answer("Verified!")
    else:
        await callback.answer("You must join both chats first.", show_alert=True)


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_edit(callback, "🏠 Main Menu", home_keyboard(callback.from_user.id == OWNER_ID))
    await callback.answer()


@dp.callback_query(F.data == "howto")
async def howto(callback: CallbackQuery):
    await safe_edit(
        callback,
        "📸 How to archive media\n\n"
        "1. Start this bot.\n"
        "2. Connect it in Telegram Settings → Telegram Business → Chatbots.\n"
        "3. In any chat of your Business account, reply to a supported media "
        f"message with: {TRIGGER_WORD}\n"
        "4. The bot sends the copy to your private chat with the bot.\n\n"
        "Only you (the account owner) can trigger it, and only media accessible "
        "to the bot can be retrieved.",
        home_keyboard(callback.from_user.id == OWNER_ID)
    )
    await callback.answer()


@dp.callback_query(F.data == "mypremium")
async def my_premium(callback: CallbackQuery):
    uid = callback.from_user.id
    expiry = premium_expiry(uid)
    if expiry:
        text = f"⭐ Premium active until: {expiry.strftime('%Y-%m-%d %H:%M UTC')}\nDaily limit: unlimited"
    else:
        text = f"🎃Yᴏᴜ Aʀᴇ Oɴ Tʜᴇ Fʀᴇᴇ Pʟᴀɴ.\nDᴀɪʟʏ Lɪᴍɪᴛ ‣ {user_daily_limit(uid)} Aʀᴄʜɪᴠᴇs."
    await safe_edit(callback, text, home_keyboard(uid == OWNER_ID))
    await callback.answer()


@dp.callback_query(F.data == "mystats")
async def my_stats(callback: CallbackQuery):
    uid = callback.from_user.id
    with db() as con:
        total = con.execute("SELECT COUNT(*) n FROM archives WHERE user_id=?", (uid,)).fetchone()["n"]
    limit = user_daily_limit(uid)
    limit_text = "unlimited" if limit is None else str(limit)
    await safe_edit(
        callback,
        f"📊 Your statistics\n\nTotal archived: {total}\n"
        f"Today: {daily_count(uid)} / {limit_text}",
        home_keyboard(uid == OWNER_ID)
    )
    await callback.answer()


# ----------------------------------------------------------------------------
# Admin panel
# ----------------------------------------------------------------------------
@dp.callback_query(F.data == "admin")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("Not allowed.", show_alert=True)
    await safe_edit(callback, "🛠 Admin Panel", admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "astats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("Not allowed.", show_alert=True)
    day_start = now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    with db() as con:
        total = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        active = con.execute("SELECT COUNT(*) n FROM users WHERE last_seen>=?",
                             ((now() - timedelta(days=7)).isoformat(),)).fetchone()["n"]
        premium = con.execute("SELECT COUNT(*) n FROM premium WHERE expires_at>?",
                              (now().isoformat(),)).fetchone()["n"]
        banned = con.execute("SELECT COUNT(*) n FROM users WHERE banned=1").fetchone()["n"]
        joined = con.execute("SELECT COUNT(*) n FROM users WHERE joined_at>=?", (day_start,)).fetchone()["n"]
        archived = con.execute("SELECT COALESCE(SUM(count),0) n FROM usage_daily WHERE day=?",
                               (today(),)).fetchone()["n"]
    await safe_edit(
        callback,
        f"📊 ADMIN STATISTICS\n\n👥 Total users: {total}\n"
        f"🟢 Active in last 7 days: {active}\n⭐ Premium users: {premium}\n"
        f"🚫 Banned users: {banned}\n🆕 Joined today: {joined}\n"
        f"📸 Archives today: {archived}",
        admin_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "ausers")
async def admin_users(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("Not allowed.", show_alert=True)
    with db() as con:
        rows = con.execute(
            "SELECT user_id, username, first_name FROM users ORDER BY joined_at DESC LIMIT 25"
        ).fetchall()
    lines = ["👥 Latest 25 registered users:"]
    for row in rows:
        label = f"@{row['username']}" if row["username"] else (row["first_name"] or "unknown")
        lines.append(f"• {html.escape(label)} — <code>{row['user_id']}</code>")
    await safe_edit(callback, "\n".join(lines), admin_keyboard(), parse_mode="HTML")
    await callback.answer()


@dp.callback_query(F.data == "apremium")
async def admin_premium_list(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("Not allowed.", show_alert=True)
    with db() as con:
        rows = con.execute("""
            SELECT u.username, u.user_id, p.expires_at
            FROM premium p JOIN users u ON u.user_id=p.user_id
            WHERE p.expires_at>? ORDER BY p.expires_at
        """, (now().isoformat(),)).fetchall()
    lines = ["⭐ Active premium users:"]
    if not rows:
        lines.append("(none)")
    for row in rows[:40]:
        name = f"@{row['username']}" if row["username"] else str(row["user_id"])
        lines.append(f"• {name} — until {row['expires_at'][:10]}")
    await safe_edit(callback, "\n".join(lines), admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "auserinfo")
async def admin_userinfo_help(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("Not allowed.", show_alert=True)
    await safe_edit(
        callback,
        "🔎 Usᴇ: \n/userinfo @username\n\nOᴛʜᴇʀ Aᴅᴍɪɴ Cᴏᴍᴍᴀɴᴅs:\n"
        "/premium @username days\n/removepremium @username\n"
        "/setlimit @username number|default\n/ban @username\n/unban @username",
        admin_keyboard()
    )
    await callback.answer()


@dp.callback_query(F.data == "aban")
async def admin_ban_help(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("Not allowed.", show_alert=True)
    await safe_edit(callback, "🚫 Use:\n/ban @username\n/unban @username", admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "abroadcast")
async def broadcast_prompt(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != OWNER_ID:
        return await callback.answer("Not allowed.", show_alert=True)
    await state.set_state(AdminFlow.broadcast_text)
    await callback.message.answer("Send the text to broadcast to all registered, non-banned users. Send /cancel to stop.")
    await callback.answer()


@dp.message(AdminFlow.broadcast_text)
async def do_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    if message.text == "/cancel":
        await state.clear()
        return await message.answer("Broadcast cancelled.")
    if not message.text:
        return await message.answer("Please send a text message for the broadcast.")
    with db() as con:
        ids = [r["user_id"] for r in con.execute("SELECT user_id FROM users WHERE banned=0")]
    sent = failed = 0
    status = await message.answer(f"Broadcasting to {len(ids)} users...")
    for user_id in ids:
        try:
            await bot.send_message(user_id, message.text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)  # Gentle rate limiting.
    with db() as con:
        con.execute(
            "INSERT INTO broadcasts(admin_id, body, sent_count, failed_count, created_at) VALUES(?,?,?,?,?)",
            (OWNER_ID, message.text, sent, failed, now().isoformat()))
    await state.clear()
    await status.edit_text(f"✅ Bʀᴏᴀᴅᴄᴀsᴛ Fɪɴɪsʜᴇᴅ❗️\nDᴇʟɪᴠᴇʀᴇᴅ ‣ {sent}\nFᴀɪʟᴇᴅ ‣ {failed}")


# ---- admin commands ---------------------------------------------------------
@dp.message(Command("premium"))
async def give_premium(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) < 1:
        return await message.answer("Usage: /premium @username days\nExample: /premium @example 30")
    user = find_registered_user(parts[1])
    if not user:
        return await message.answer("Username not found. The user must start this bot first, and their username must be visible.")
    expiry = now() + timedelta(days=int(parts[2]))
    with db() as con:
        con.execute(
            "INSERT INTO premium(user_id, expires_at) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at",
            (user["user_id"], expiry.isoformat()))
    await message.answer(f"⭐ Pʀᴇᴍɪᴜᴍ Gʀᴀɴᴛᴇᴅ Tᴏ @{user['username'] or user['user_id']} until {expiry.date()}❗️")


@dp.message(Command("removepremium"))
async def remove_premium(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        return await message.answer("Usage: /removepremium @username")
    user = find_registered_user(parts[1])
    if not user:
        return await message.answer("User not found. They must have started the bot.")
    with db() as con:
        con.execute("DELETE FROM premium WHERE user_id=?", (user["user_id"],))
    await message.answer(f"Premium removed for @{user['username'] or user['user_id']}.")


@dp.message(Command("setlimit"))
async def set_limit(message: Message):
    """/setlimit @username 50   -> custom daily limit for that user only
       /setlimit @username default -> back to the default limit"""
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or not (parts[2].isdigit() or parts[2].lower() == "default"):
        return await message.answer("Usage: /setlimit @username number\n/setlimit @username default")
    user = find_registered_user(parts[1])
    if not user:
        return await message.answer("User not found. They must have started the bot.")
    value = None if parts[2].lower() == "default" else int(parts[2])
    with db() as con:
        con.execute("UPDATE users SET custom_limit=? WHERE user_id=?", (value, user["user_id"]))
    shown = f"{value}/day" if value is not None else f"default ({FREE_DAILY_LIMIT}/day)"
    await message.answer(f"✅ Daily limit for @{user['username'] or user['user_id']}: {shown}")


@dp.message(Command("userinfo"))
async def user_info(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        return await message.answer("Usage: /userinfo @username")
    user = find_registered_user(parts[1])
    if not user:
        return await message.answer("User not found.")
    uid = user["user_id"]
    expiry = premium_expiry(uid)
    limit = user_daily_limit(uid)
    with db() as con:
        total = con.execute("SELECT COUNT(*) n FROM archives WHERE user_id=?", (uid,)).fetchone()["n"]
        conns = con.execute("SELECT COUNT(*) n FROM business_connections WHERE owner_id=?", (uid,)).fetchone()["n"]
    await message.answer(
        f"User: @{user['username'] or 'no_username'}\n"
        f"ID: {uid}\nBanned: {bool(user['banned'])}\n"
        f"Premium: {expiry.isoformat() if expiry else 'No'}\n"
        f"Daily limit: {'unlimited' if limit is None else limit}\n"
        f"Used today: {daily_count(uid)}\n"
        f"Total archived: {total}\n"
        f"Business connections: {conns}\n"
        f"Last seen: {user['last_seen']}"
    )


@dp.message(Command("ban"))
async def ban_user(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        return await message.answer("Usage: /ban @username")
    user = find_registered_user(parts[1])
    if not user:
        return await message.answer("User not found.")
    with db() as con:
        con.execute("UPDATE users SET banned=1 WHERE user_id=?", (user["user_id"],))
    await message.answer("🚫 Usᴇʀ Bᴀɴɴᴇᴅ ❗")


@dp.message(Command("unban"))
async def unban_user(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    parts = (message.text or "").split()
    if len(parts) != 2:
        return await message.answer("Usage: /unban @username")
    user = find_registered_user(parts[1])
    if not user:
        return await message.answer("User not found.")
    with db() as con:
        con.execute("UPDATE users SET banned=0 WHERE user_id=?", (user["user_id"],))
    await message.answer("✅ User unbanned.")


# ----------------------------------------------------------------------------
# Archiving (Business messages)
# ----------------------------------------------------------------------------
CAPTION = (
    
    "╭──────────────╮\n"
    "Mᴇᴅɪᴀ Sᴜᴄᴄᴇssғᴜʟʟʏ Aʀᴄʜɪᴠᴇᴅ❗️\n"
    "👾 Bᴏᴛ‣ @ErebusSpyBot\n"
    "🎗 Dᴇᴠᴇʟᴏᴘᴇʀ ‣ @Noxmirage\n"
    "╰──────────────╯"
)


def extract_media(original: Message):
    if original.photo:
        return original.photo[-1], "photo", ".jpg"
    if original.video:
        return original.video, "video", ".mp4"
    if original.animation:
        return original.animation, "animation", ".mp4"
    if original.document:
        suffix = Path(original.document.file_name or "").suffix
        return original.document, "document", suffix
    if original.video_note:
        return original.video_note, "video_note", ".mp4"
    return None, None, None


async def deliver(media_type: str, chat_id: int, upload: FSInputFile):
    common = dict(chat_id=chat_id, reply_markup=media_keyboard())
    cap = dict(caption=CAPTION, parse_mode="HTML")
    if media_type == "photo":
        await bot.send_photo(photo=upload, **cap, **common)
    elif media_type == "video":
        await bot.send_video(video=upload, **cap, **common)
    elif media_type == "animation":
        await bot.send_animation(animation=upload, **cap, **common)
    elif media_type == "document":
        await bot.send_document(document=upload, **cap, **common)
    elif media_type == "video_note":
        await bot.send_video_note(video_note=upload, **common)  # video notes have no caption


@dp.business_message()
async def archive_reply(message: Message):
    # 1. Must be the trigger word, sent as a reply, with a business connection.
    if not message.text or message.text.strip().casefold() != TRIGGER_WORD:
        return
    if not message.business_connection_id or not message.from_user:
        return
    original = message.reply_to_message
    if original is None:
        return

    connection = get_connection(message.business_connection_id)
    if not connection:
        logging.warning("Unknown/disabled Business connection %s", message.business_connection_id)
        return

    owner_id = connection["owner_id"]

    # 2. Only the owner of THIS business account may trigger an archive.
    #    (If two connected accounts chat with each other, Telegram delivers the same
    #    message once per connection -- this check stops the double handling,
    #    which is what caused two pictures / the stray "limit reached" message.)
    if message.from_user.id != owner_id:
        return

    # 3. The owner must have started the bot and not be banned.
    owner = get_user(owner_id)
    if not owner:
        logging.warning("Business owner %s has not started the bot", owner_id)
        return
    if owner["banned"]:
        return

    # 4. Is there media in the replied message?
    media, media_type, extension = extract_media(original)
    if media is None:
        await bot.send_message(owner_id, "❌ No supported media found in the replied message.")
        return

    # 5. Never process the same trigger twice.
    try:
        with db() as con:
            con.execute(
                "INSERT INTO processed_messages(connection_id, source_chat_id, command_message_id, processed_at) "
                "VALUES (?, ?, ?, ?)",
                (message.business_connection_id, message.chat.id, message.message_id, now().isoformat()))
    except sqlite3.IntegrityError:
        logging.warning("Duplicate request ignored")
        return

    def forget_trigger():
        with db() as con:
            con.execute(
                "DELETE FROM processed_messages WHERE connection_id=? AND source_chat_id=? AND command_message_id=?",
                (message.business_connection_id, message.chat.id, message.message_id))

    # 6. Per-user daily limit (each owner has their own counter).
    if not has_quota(owner_id):
        limit = user_daily_limit(owner_id)
        try:
            await bot.send_message(
                owner_id,
                f"Daily limit reached! You can archive {limit} media files per day. "
                "Your limit resets at 00:00 UTC."
            )
        except Exception:
            logging.exception("Could not send limit notice to %s", owner_id)
        return

    # 7. Download, deliver to the owner, then count it.
    user_dir = SAVE_DIR / str(owner_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    filepath = user_dir / f"{message.chat.id}_{original.message_id}_{media_type}{extension}"

    try:
        await bot.download(media.file_id, destination=filepath)
        await deliver(media_type, owner_id, FSInputFile(filepath))

        with db() as con:
            con.execute(
                "INSERT INTO archives(user_id, source_chat_id, source_message_id, media_type, file_path, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (owner_id, message.chat.id, original.message_id, media_type, str(filepath), now().isoformat()))
            con.execute(
                "INSERT INTO usage_daily(user_id, day, count) VALUES (?, ?, 1) "
                "ON CONFLICT(user_id, day) DO UPDATE SET count = count + 1",
                (owner_id, today()))

        logging.info("ARCHIVED %s | owner=%s | connection=%s | chat=%s | trigger_msg=%s | used_today=%s",
                     media_type, owner_id, message.business_connection_id,
                     message.chat.id, message.message_id, daily_count(owner_id))

    except Exception:
        logging.exception("Media download or delivery failed")
        forget_trigger()  # failed attempts don't count and can be retried
        try:
            await bot.send_message(owner_id, "❌ Media retrieval failed. Please try again.")
        except Exception:
            pass


# ----------------------------------------------------------------------------
async def main():
    init_db()
    print("Media Archive Bot Started Successfully!")
    print("Bot is running...")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "business_connection", "business_message", "callback_query"]
    )


if __name__ == "__main__":
    asyncio.run(main())
