import asyncio
import html
import json
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, BufferedInputFile, BusinessConnection,
)

try:
    import qrcode  # pip install "qrcode[pil]"
except ImportError:
    qrcode = None

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

# Payments (UPI). PLANS = {months: (days of premium, price in INR)}
UPI_ID = os.getenv("UPI_ID", "Ahmadammar@fam")
UPI_NAME = os.getenv("UPI_NAME", "Erebus Premium")
PLANS = {1: (30, 29), 2: (60, 39), 3: (90, 49)}
MAX_PENDING_PAYMENTS = 3  # per user, stops screenshot spam

# Animated / custom emojis (optional). IDs are stored in this file, see /emojis.
EMOJI_FILE = Path(os.getenv("EMOJI_FILE", "emoji_ids.json"))
USE_CUSTOM_EMOJI = os.getenv("USE_CUSTOM_EMOJI", "1") == "1"  # kill switch

if not BOT_TOKEN or not OWNER_ID:
    raise RuntimeError("Set BOT_TOKEN and OWNER_ID in your .env file")

logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    force=True,
)
logging.getLogger("aiogram").setLevel(logging.INFO)

# Every message is HTML by default (pass parse_mode=None for raw admin text).
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()


# ----------------------------------------------------------------------------
# UI kit: font, emojis, panels, buttons, auto-deleting messages
# ----------------------------------------------------------------------------
_SMALL_CAPS = str.maketrans(
    "abcdefghijklmnopqrstuvwxyz",
    "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ",
)


def sc(text):
    """Bot font: 'free plan' -> 'Fʀᴇᴇ Pʟᴀɴ'. First letter of each word is a capital;
    ACRONYMS, numbers, @names and /commands stay untouched."""
    def word(w):
        if any(c.isdigit() or c in "@/₹_<>&" for c in w) or (w.isupper() and len(w) > 1):
            return w
        m = re.search(r"[A-Za-z]", w)
        if not m:
            return w
        i = m.end()
        return w[:i - 1] + w[i - 1].upper() + w[i:].translate(_SMALL_CAPS)
    return "".join(p if p.isspace() else word(p) for p in re.split(r"(\s+)", text))


# Every emoji the UI uses has a NAME and a normal-emoji fallback. If you give a name a
# custom (animated) emoji ID it is used everywhere that name appears, in text and buttons.
EMOJI = {
    "title": "✨", "star": "⭐", "camera": "📸", "bolt": "⚡", "user": "👤", "id": "🆔",
    "bot": "👾", "dev": "👨‍💻", "ok": "✅", "err": "❌", "warn": "⚠️", "time": "⏱",
    "pay": "💳", "box": "📦", "money": "💰", "calendar": "📅", "bell": "🔔", "ban": "🚫",
    "admin": "🛠", "mail": "✉️", "link": "🔗", "channel": "📢", "group": "👥",
    "stats": "📊", "lock": "🔒", "ghost": "👻", "home": "🏠", "bank": "🏦",
    "party": "🎉", "pin": "📝", "spin": "🌀",
}
EMOJI_IDS = {}


def load_emoji_ids():
    EMOJI_IDS.clear()
    if not USE_CUSTOM_EMOJI:
        return
    try:
        data = json.loads(EMOJI_FILE.read_text(encoding="utf-8"))
        EMOJI_IDS.update({k: str(v) for k, v in data.items() if k in EMOJI})
    except FileNotFoundError:
        pass
    except Exception:
        logging.exception("Could not read %s", EMOJI_FILE)


def save_emoji_ids():
    EMOJI_FILE.write_text(json.dumps(EMOJI_IDS, indent=2), encoding="utf-8")


def E(name):
    """Emoji for use inside HTML text (custom animated one if configured)."""
    fallback = EMOJI.get(name, "•")
    cid = EMOJI_IDS.get(name)
    return f'<tg-emoji emoji-id="{cid}">{fallback}</tg-emoji>' if cid else fallback


_BTN_FIELDS = InlineKeyboardButton.model_fields
HAS_ICON = "icon_custom_emoji_id" in _BTN_FIELDS   # needs aiogram >= 3.25
HAS_STYLE = "style" in _BTN_FIELDS


def btn(text, emoji=None, style=None, **kw):
    """Inline button in the bot font with an icon.
    style: 'primary' (blue) / 'success' (green) / 'danger' (red)."""
    label = sc(text)
    extra = {}
    cid = EMOJI_IDS.get(emoji) if emoji else None
    if cid and HAS_ICON:
        extra["icon_custom_emoji_id"] = cid
    elif emoji:
        label = f"{EMOJI.get(emoji, '')} {label}"
    if style and HAS_STYLE:
        extra["style"] = style
    return InlineKeyboardButton(text=label, **extra, **kw)


SEP = "──────────────────"


def bq(text):
    return f"<blockquote>{text}</blockquote>"


def head(emoji, title, tail=""):
    return f"{E(emoji)} <b>{sc(title)}</b>{tail}"


def row(emoji, label, value):
    return f"{E(emoji)} <b>{sc(label)}</b> ➛ {value}"


def esc(value):
    return html.escape(str(value))


# ---- auto-cleaning of messages that are no longer important ------------------
_TRASH = {}     # chat_id -> ids of temporary messages (prompts, QR pictures ...)
_PANEL = {}     # chat_id -> id of the current main-menu message
_TASKS = set()  # keeps delayed-delete tasks alive


async def _delete(chat_id, message_id):
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass  # already gone / too old / not allowed


def remember(message):
    """Mark a message as temporary: sweep() removes it when the flow is over."""
    if message:
        _TRASH.setdefault(message.chat.id, []).append(message.message_id)
    return message


async def sweep(chat_id, keep=None):
    for mid in _TRASH.pop(chat_id, []):
        if mid != keep:
            await _delete(chat_id, mid)


async def tidy(message):
    """Delete the user's own message (a command or typed input) to keep the chat clean."""
    await _delete(message.chat.id, message.message_id)


async def _delete_later(chat_id, message_id, ttl):
    await asyncio.sleep(ttl)
    await _delete(chat_id, message_id)


async def notice(chat_id, text, ttl=8, **kw):
    """Send a short-lived message that removes itself after `ttl` seconds."""
    try:
        msg = await bot.send_message(chat_id, text, **kw)
    except Exception:
        logging.exception("notice to %s failed", chat_id)
        return None
    task = asyncio.create_task(_delete_later(chat_id, msg.message_id, ttl))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return msg


async def show_panel(chat_id, text, markup):
    """Send a fresh main panel and remove the previous one (no stacked menus)."""
    old = _PANEL.pop(chat_id, None)
    if old:
        await _delete(chat_id, old)
    msg = await bot.send_message(chat_id, text, reply_markup=markup)
    _PANEL[chat_id] = msg.message_id
    return msg


async def safe_edit(callback: CallbackQuery, text, reply_markup=None, parse_mode="HTML"):
    """Edit the menu in place. Photo messages (the QR) can't become text, so they are
    replaced; 'message is not modified' is ignored."""
    msg = callback.message
    if getattr(msg, "photo", None):
        await _delete(msg.chat.id, msg.message_id)
        sent = await bot.send_message(msg.chat.id, text, reply_markup=reply_markup,
                                      parse_mode=parse_mode)
        _PANEL[msg.chat.id] = sent.message_id
        return
    try:
        await msg.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise


# ----------------------------------------------------------------------------
# Keyboards (one wide button per row, like a proper panel)
# ----------------------------------------------------------------------------
def media_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("Noxmirage", "ghost", url="https://t.me/Noxmirage")],
        [btn("Hazen Nexus", "channel", url="https://t.me/HazenNexus")],
        [btn("Erebus Redirect", "link", url="https://t.me/Erebusredirect")],
    ])


def home_keyboard(is_admin=False):
    rows = [
        [btn("How To Archive", "camera", callback_data="howto"),
         btn("My Premium", "star", callback_data="mypremium")],
        [btn("My Statistics", "stats", callback_data="mystats"),
         btn("Membership", "ok", callback_data="checkjoin")],
        [btn("Buy Premium", "pay", style="primary", callback_data="buy")],
        [btn("Updates", "channel", url="https://t.me/linksfromerebus")],
    ]
    if is_admin:
        rows.append([btn("Admin Panel", "admin", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard(*top_rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        *top_rows, [btn("Main Menu", "home", callback_data="home")]])


def join_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("Join Channel", "channel", url="https://t.me/linksfromerebus")],
        [btn("Join Group", "group", url="https://t.me/ecochamber")],
        [btn("I've Joined", "ok", style="success", callback_data="checkjoin")],
    ])


def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("Statistics", "stats", callback_data="astats"),
         btn("Users", "user", callback_data="ausers")],
        [btn("Premium Users", "star", callback_data="apremium"),
         btn("Broadcast", "channel", callback_data="abroadcast")],
        [btn("User Info", "id", callback_data="auserinfo"),
         btn("Ban / Unban", "ban", style="danger", callback_data="aban")],
        [btn("Message One User", "mail", callback_data="amsg")],
        [btn("Main Menu", "home", callback_data="home")],
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
# payments              UPI payment requests (pending / approved / rejected)
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
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            months INTEGER NOT NULL,
            days INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            screenshot_file_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',   -- pending / approved / rejected
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id, status);
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


def grant_premium(user_id, days, extend=True):
    """Give premium. extend=True adds on top of any remaining premium time."""
    base = now()
    if extend:
        current = premium_expiry(user_id)
        if current and current > base:
            base = current
    expiry = base + timedelta(days=days)
    with db() as con:
        con.execute(
            "INSERT INTO premium(user_id, expires_at) VALUES(?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at",
            (user_id, expiry.isoformat()))
    return expiry


def pending_payments(user_id=None):
    with db() as con:
        if user_id is None:
            return con.execute("SELECT COUNT(*) n FROM payments WHERE status='pending'").fetchone()["n"]
        return con.execute(
            "SELECT COUNT(*) n FROM payments WHERE user_id=? AND status='pending'", (user_id,)
        ).fetchone()["n"]


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
# Screens (text)
# ----------------------------------------------------------------------------
def home_text(user_id, name):
    expiry = premium_expiry(user_id)
    limit = user_daily_limit(user_id)
    plan = f"{sc('Premium')} ({expiry:%d %b %Y})" if expiry else sc("Free")
    used = f"{daily_count(user_id)} / {'∞' if limit is None else limit}"
    return "\n".join([
        bq(f"{E('user')} {sc('Welcome')}, {esc(name or 'friend')}"),
        head("title", "Media Archive Bot", " ❗️"),
        "",
        row("star", "Plan", plan),
        row("camera", "Today", used),
        SEP,
        f"{E('bolt')} {sc('Reply to any media with')} <code>{TRIGGER_WORD}</code>",
    ])


def howto_text():
    return "\n".join([
        bq(f"{E('camera')} {sc('How to archive media')}"),
        "",
        row("pin", "Step 1", sc("Start this bot")),
        row("pin", "Step 2", sc("Settings → Business → Chatbots → add this bot")),
        row("pin", "Step 3", f"{sc('In any chat, reply to a media with')} <code>{TRIGGER_WORD}</code>"),
        row("box", "Step 4", sc("The copy arrives here, in your private chat")),
        SEP,
        f"{E('lock')} {sc('Only you (the account owner) can trigger it.')}",
    ])


def join_text(first_visit=True):
    title = "Join To Unlock" if first_visit else "Not Joined Yet"
    return "\n".join([
        bq(f"{E('lock')} {sc('Access locked')}"),
        head("channel", title, " ❗️"),
        "",
        sc("Join our channel and group, then tap I've Joined."),
    ])


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


async def notify_premium(user_id, expiry):
    """Tell the user their premium is active. Returns False if they can't be reached."""
    text = "\n".join([
        bq(f"{E('party')} {sc('Congratulations!')}"),
        head("star", "Premium Activated", " ❗️"),
        "",
        row("calendar", "Valid Till", f"{expiry:%Y-%m-%d %H:%M} UTC"),
        row("bolt", "Daily Limit", sc("Unlimited")),
    ])
    try:
        await bot.send_message(user_id, text, reply_markup=back_keyboard())
        return True
    except Exception:
        logging.exception("Could not notify %s about premium", user_id)
        return False


class AdminFlow(StatesGroup):
    broadcast_text = State()
    dm_target = State()
    dm_text = State()


class PayFlow(StatesGroup):
    screenshot = State()


async def admin_only(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("Not allowed.", show_alert=True)
        return False
    return True


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
async def start(message: Message, state: FSMContext):
    await state.clear()
    if not message.from_user:
        return
    upsert_user(message.from_user)
    chat = message.chat.id
    await tidy(message)          # remove the "/start" the user typed
    await sweep(chat)            # remove leftover prompts / QR pictures
    if is_banned(message.from_user.id):
        await notice(chat, f"{E('ban')} {sc('Your access has been disabled.')}", ttl=15)
        return
    if not await membership_ok(message.from_user.id):
        await show_panel(chat, join_text(), join_keyboard())
        return
    uid = message.from_user.id
    await show_panel(chat, home_text(uid, message.from_user.first_name),
                     home_keyboard(uid == OWNER_ID))


@dp.callback_query(F.data == "checkjoin")
async def check_join(callback: CallbackQuery):
    if not callback.from_user:
        return
    upsert_user(callback.from_user)
    uid = callback.from_user.id
    if is_banned(uid):
        await callback.answer("Access disabled.", show_alert=True)
        return
    if await membership_ok(uid):
        await safe_edit(callback, home_text(uid, callback.from_user.first_name),
                        home_keyboard(uid == OWNER_ID))
        await callback.answer("Verified!")
    else:
        await callback.answer("You must join both chats first.", show_alert=True)


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    chat = callback.message.chat.id
    await sweep(chat, keep=callback.message.message_id)
    uid = callback.from_user.id
    await safe_edit(callback, home_text(uid, callback.from_user.first_name),
                    home_keyboard(uid == OWNER_ID))
    await callback.answer()


@dp.callback_query(F.data == "howto")
async def howto(callback: CallbackQuery):
    await safe_edit(callback, howto_text(), back_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "mypremium")
async def my_premium(callback: CallbackQuery):
    uid = callback.from_user.id
    expiry = premium_expiry(uid)
    if expiry:
        text = "\n".join([
            bq(f"{E('star')} {sc('Premium member')}"),
            "",
            row("calendar", "Active Until", f"{expiry:%Y-%m-%d %H:%M} UTC"),
            row("bolt", "Daily Limit", sc("Unlimited")),
            row("camera", "Used Today", daily_count(uid)),
        ])
        kb = back_keyboard([btn("Extend Premium", "pay", style="primary", callback_data="buy")])
    else:
        text = "\n".join([
            bq(f"{E('user')} {sc('You are on the free plan')}"),
            "",
            row("bolt", "Daily Limit", user_daily_limit(uid)),
            row("camera", "Used Today", daily_count(uid)),
            SEP,
            f"{E('star')} {sc('Go premium for unlimited archives.')}",
        ])
        kb = back_keyboard([btn("Buy Premium", "pay", style="primary", callback_data="buy")])
    await safe_edit(callback, text, kb)
    await callback.answer()


@dp.callback_query(F.data == "mystats")
async def my_stats(callback: CallbackQuery):
    uid = callback.from_user.id
    with db() as con:
        total = con.execute("SELECT COUNT(*) n FROM archives WHERE user_id=?", (uid,)).fetchone()["n"]
    limit = user_daily_limit(uid)
    text = "\n".join([
        bq(f"{E('stats')} {sc('Your statistics')}"),
        "",
        row("box", "Total Archived", total),
        row("camera", "Today", f"{daily_count(uid)} / {'∞' if limit is None else limit}"),
    ])
    await safe_edit(callback, text, back_keyboard())
    await callback.answer()


# ----------------------------------------------------------------------------
# Admin panel
# ----------------------------------------------------------------------------
def admin_text():
    return "\n".join([
        bq(f"{E('admin')} {sc('Admin panel')}"),
        "",
        row("pay", "Pending Payments", pending_payments()),
    ])


@dp.callback_query(F.data == "admin")
async def admin_panel(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    await safe_edit(callback, admin_text(), admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "astats")
async def admin_stats(callback: CallbackQuery):
    if not await admin_only(callback):
        return
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
    text = "\n".join([
        bq(f"{E('stats')} {sc('Admin statistics')}"),
        "",
        row("group", "Total Users", total),
        row("bolt", "Active 7 Days", active),
        row("star", "Premium Users", premium),
        row("ban", "Banned Users", banned),
        row("title", "Joined Today", joined),
        row("camera", "Archives Today", archived),
        row("pay", "Pending Payments", pending_payments()),
    ])
    await safe_edit(callback, text, admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "ausers")
async def admin_users(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    with db() as con:
        rows = con.execute(
            "SELECT user_id, username, first_name FROM users ORDER BY joined_at DESC LIMIT 25"
        ).fetchall()
    lines = [bq(f"{E('group')} {sc('Latest 25 users')}"), ""]
    for r in rows:
        label = f"@{r['username']}" if r["username"] else (r["first_name"] or "unknown")
        lines.append(f"{E('user')} {esc(label)} ➛ <code>{r['user_id']}</code>")
    await safe_edit(callback, "\n".join(lines), admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "apremium")
async def admin_premium_list(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    with db() as con:
        rows = con.execute("""
            SELECT u.username, u.user_id, p.expires_at
            FROM premium p JOIN users u ON u.user_id=p.user_id
            WHERE p.expires_at>? ORDER BY p.expires_at
        """, (now().isoformat(),)).fetchall()
    lines = [bq(f"{E('star')} {sc('Active premium users')}"), ""]
    if not rows:
        lines.append(sc("None yet."))
    for r in rows[:40]:
        name = f"@{r['username']}" if r["username"] else str(r["user_id"])
        lines.append(f"{E('star')} {esc(name)} ➛ {r['expires_at'][:10]}")
    await safe_edit(callback, "\n".join(lines), admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "auserinfo")
async def admin_userinfo_help(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    text = "\n".join([
        bq(f"{E('id')} {sc('Admin commands')}"),
        "",
        "<code>/userinfo @username</code>",
        "<code>/premium @username days</code>",
        "<code>/removepremium @username</code>",
        "<code>/setlimit @username number|default</code>",
        "<code>/msg @username your message</code>",
        "<code>/ban @username</code>  <code>/unban @username</code>",
        "<code>/emojis</code>",
    ])
    await safe_edit(callback, text, admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "aban")
async def admin_ban_help(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    text = "\n".join([
        bq(f"{E('ban')} {sc('Ban / unban')}"),
        "",
        "<code>/ban @username</code>",
        "<code>/unban @username</code>",
    ])
    await safe_edit(callback, text, admin_keyboard())
    await callback.answer()


# ---- broadcast ----------------------------------------------------------------
@dp.callback_query(F.data == "abroadcast")
async def broadcast_prompt(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    await state.set_state(AdminFlow.broadcast_text)
    remember(await callback.message.answer(
        f"{E('channel')} {sc('Send the text to broadcast to all users.')}\n{sc('Send /cancel to stop.')}"))
    await callback.answer()


@dp.message(AdminFlow.broadcast_text)
async def do_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    if message.text == "/cancel":
        await state.clear()
        await tidy(message)
        await sweep(chat)
        return await notice(chat, f"{E('err')} {sc('Broadcast cancelled.')}")
    if not message.text:
        return await notice(chat, f"{E('warn')} {sc('Please send a text message.')}", ttl=6)
    text = message.text
    await tidy(message)
    await sweep(chat)
    with db() as con:
        ids = [r["user_id"] for r in con.execute("SELECT user_id FROM users WHERE banned=0")]
    sent = failed = 0
    status = await bot.send_message(chat, f"{E('spin')} {sc('Broadcasting to')} {len(ids)} {sc('users...')}")
    for user_id in ids:
        try:
            await bot.send_message(user_id, text, parse_mode=None)  # raw text, as typed
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)  # Gentle rate limiting.
    with db() as con:
        con.execute(
            "INSERT INTO broadcasts(admin_id, body, sent_count, failed_count, created_at) VALUES(?,?,?,?,?)",
            (OWNER_ID, text, sent, failed, now().isoformat()))
    await state.clear()
    await status.edit_text("\n".join([
        head("ok", "Broadcast Finished", " ❗️"), "",
        row("ok", "Delivered", sent), row("err", "Failed", failed)]))
    task = asyncio.create_task(_delete_later(chat, status.message_id, 60))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)


# ---- message ONE user (button flow, or /msg @username text) -----------------------
def resolve_target(token):
    """@username (must have started the bot) or numeric ID -> (user_id, label) or None."""
    token = token.strip()
    if token.isdigit():
        row_ = get_user(int(token))
        label = f"@{row_['username']}" if row_ and row_["username"] else token
        return int(token), label
    row_ = find_registered_user(token)
    if not row_:
        return None
    return row_["user_id"], (f"@{row_['username']}" if row_["username"] else str(row_["user_id"]))


async def send_direct(user_id, text):
    try:
        await bot.send_message(user_id, text, parse_mode=None)  # raw text, as typed
        return True
    except Exception:
        logging.exception("Direct message to %s failed", user_id)
        return False


@dp.callback_query(F.data == "amsg")
async def dm_prompt(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    await state.set_state(AdminFlow.dm_target)
    remember(await callback.message.answer(
        f"{E('mail')} {sc('Send the @username or numeric ID of the person to message.')}\n"
        f"{sc('Send /cancel to stop.')}"))
    await callback.answer()


@dp.message(AdminFlow.dm_target)
async def dm_got_target(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    if message.text == "/cancel":
        await state.clear()
        await sweep(chat)
        return await notice(chat, f"{E('err')} {sc('Cancelled.')}", ttl=5)
    if not message.text:
        return await notice(chat, f"{E('warn')} {sc('Please send a @username or user ID.')}", ttl=6)
    target = resolve_target(message.text)
    if not target:
        return await notice(
            chat, f"{E('warn')} {sc('User not found. They must have started the bot. Try again or /cancel.')}",
            ttl=8)
    await sweep(chat)
    await state.update_data(uid=target[0], label=target[1])
    await state.set_state(AdminFlow.dm_text)
    remember(await message.answer(f"{E('pin')} {sc('Now send the message for')} {esc(target[1])}"))


@dp.message(AdminFlow.dm_text)
async def dm_got_text(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    if message.text == "/cancel":
        await state.clear()
        await sweep(chat)
        return await notice(chat, f"{E('err')} {sc('Cancelled.')}", ttl=5)
    if not message.text:
        return await notice(chat, f"{E('warn')} {sc('Please send a text message.')}", ttl=6)
    data = await state.get_data()
    await state.clear()
    await sweep(chat)
    ok = await send_direct(data["uid"], message.text)
    label = esc(data["label"])
    await notice(chat, f"{E('ok')} {sc('Message delivered to')} {label}" if ok
                 else f"{E('err')} {sc('Could not deliver to')} {label}", ttl=10)


@dp.message(Command("msg"))
async def msg_command(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        return await notice(chat, "Usage: <code>/msg @username your message</code>\n"
                                  "(an ID works instead of @username)", ttl=10)
    target = resolve_target(parts[1])
    if not target:
        return await notice(chat, f"{E('warn')} {sc('User not found. They must have started the bot.')}")
    ok = await send_direct(target[0], parts[2])
    label = esc(target[1])
    await notice(chat, f"{E('ok')} {sc('Message delivered to')} {label}" if ok
                 else f"{E('err')} {sc('Could not deliver to')} {label}", ttl=10)


# ----------------------------------------------------------------------------
# Payments: plan -> custom UPI QR -> user sends screenshot -> admin approves
# ----------------------------------------------------------------------------
def month_label(months):
    return f"{months} {'Month' if months == 1 else 'Months'}"


def plans_text():
    lines = [bq(f"{E('pay')} {sc('Premium plans')}"), ""]
    for months, (_days, price) in PLANS.items():
        lines.append(row("star", month_label(months), f"₹{price}"))
    lines += [SEP, f"{E('bolt')} {sc('Unlimited archives on every plan.')}",
              f"{E('pay')} {sc('Select a plan to get your payment QR.')}"]
    return "\n".join(lines)


def plans_keyboard():
    rows = [[btn(f"{month_label(m)} — ₹{price}", "star", callback_data=f"plan:{m}")]
            for m, (_d, price) in PLANS.items()]
    rows.append([btn("Main Menu", "home", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def upi_link(price, months):
    """UPI deep link with the amount already filled in (this is what the QR encodes)."""
    note = quote(f"Premium {months}M")
    return f"upi://pay?pa={UPI_ID}&pn={quote(UPI_NAME)}&am={price}&cu=INR&tn={note}"


def make_qr(data):
    if qrcode is None:
        logging.warning("qrcode library missing: pip install \"qrcode[pil]\"")
        return None
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    buf = BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf)
    return BufferedInputFile(buf.getvalue(), filename="upi_qr.png")


@dp.callback_query(F.data == "buy")
async def buy_menu(callback: CallbackQuery, state: FSMContext):
    upsert_user(callback.from_user)
    if is_banned(callback.from_user.id):
        return await callback.answer("Access disabled.", show_alert=True)
    await state.clear()
    await sweep(callback.message.chat.id, keep=callback.message.message_id)
    await safe_edit(callback, plans_text(), plans_keyboard())  # also swaps out the QR picture
    await callback.answer()


@dp.callback_query(F.data.startswith("plan:"))
async def plan_selected(callback: CallbackQuery):
    months = int(callback.data.split(":")[1])
    plan = PLANS.get(months)
    if not plan:
        return await callback.answer("Plan not found.", show_alert=True)
    days, price = plan
    chat = callback.message.chat.id
    caption = "\n".join([
        bq(f"{E('pay')} {sc('Pay')} ₹{price} ➛ {sc(month_label(months))}"),
        "",
        row("bank", "UPI ID", f"<code>{esc(UPI_ID)}</code>"),
        row("money", "Amount", f"₹{price}"),
        row("calendar", "Validity", f"{days} {sc('days')}"),
        SEP,
        f"1️⃣ {sc('Scan this QR with any UPI app')}",
        f"2️⃣ {sc('Pay the exact amount')}",
        f"3️⃣ {sc('Tap the button below and send the screenshot')}",
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn("I've Paid — Send Screenshot", "ok", style="success", callback_data=f"paid:{months}")],
        [btn("Back To Plans", "home", callback_data="buy")],
    ])
    await sweep(chat)  # remove an older QR if they switched plans
    qr = make_qr(upi_link(price, months))
    if qr:
        sent = await callback.message.answer_photo(qr, caption=caption, reply_markup=kb)
    else:  # qrcode library not installed: still let the user pay manually
        sent = await callback.message.answer(
            caption + f"\n\n{E('warn')} {sc('QR is unavailable, please pay to the UPI ID above.')}",
            reply_markup=kb)
    remember(sent)
    await _delete(chat, callback.message.message_id)  # the plans menu is no longer needed
    await callback.answer()


@dp.callback_query(F.data.startswith("paid:"))
async def paid_clicked(callback: CallbackQuery, state: FSMContext):
    months = int(callback.data.split(":")[1])
    if months not in PLANS:
        return await callback.answer("Plan not found.", show_alert=True)
    if pending_payments(callback.from_user.id) >= MAX_PENDING_PAYMENTS:
        return await callback.answer(
            "You already have pending payments. Please wait for approval.", show_alert=True)
    await state.set_state(PayFlow.screenshot)
    await state.update_data(months=months)
    remember(await callback.message.answer(
        f"{E('camera')} {sc('Send your payment screenshot now as a photo.')}\n"
        f"{sc('Send /cancel to stop.')}"))
    await callback.answer()


@dp.message(PayFlow.screenshot, Command("cancel"))
async def pay_cancel(message: Message, state: FSMContext):
    await state.clear()
    chat = message.chat.id
    await tidy(message)
    await sweep(chat)  # QR + prompt disappear
    await notice(chat, f"{E('err')} {sc('Payment cancelled.')}", ttl=5)
    uid = message.from_user.id
    await show_panel(chat, home_text(uid, message.from_user.first_name), home_keyboard(uid == OWNER_ID))


@dp.message(PayFlow.screenshot, F.photo)
async def pay_screenshot(message: Message, state: FSMContext):
    chat = message.chat.id
    months = (await state.get_data()).get("months")
    plan = PLANS.get(months)
    if not plan:
        await state.clear()
        return await notice(chat, sc("Something went wrong. Please start again from Buy Premium."))
    days, price = plan
    user = message.from_user
    file_id = message.photo[-1].file_id
    with db() as con:
        pid = con.execute(
            "INSERT INTO payments(user_id, months, days, amount, screenshot_file_id, status, created_at) "
            "VALUES (?,?,?,?,?, 'pending', ?)",
            (user.id, months, days, price, file_id, now().isoformat())).lastrowid
    await state.clear()

    # Forward the screenshot to the admin as an update, with approve / reject buttons.
    handle = f"@{user.username}" if user.username else "no username"
    caption = "\n".join([
        bq(f"{E('pay')} {sc('New payment request')} #{pid}"),
        "",
        row("user", "User", f"{esc(user.full_name)} ({esc(handle)})"),
        row("id", "ID", f"<code>{user.id}</code>"),
        row("box", "Plan", f"{sc(month_label(months))} ({days} {sc('days')})"),
        row("money", "Amount", f"₹{price}"),
        SEP,
        sc("Check the screenshot, then approve or reject."),
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        btn("Approve", "ok", style="success", callback_data=f"payok:{pid}"),
        btn("Reject", "err", style="danger", callback_data=f"payno:{pid}"),
    ]])
    forwarded = True
    try:
        await bot.send_photo(OWNER_ID, file_id, caption=caption, reply_markup=kb)
    except Exception:
        forwarded = False
        logging.exception("Could not forward payment #%s to admin", pid)

    await sweep(chat)            # QR + "send screenshot" prompt are no longer needed
    if forwarded:
        await tidy(message)      # remove the screenshot from the user's chat
    await bot.send_message(
        chat,
        "\n".join([
            bq(f"{E('ok')} {sc('Screenshot received')}"),
            "",
            row("pin", "Payment", f"#{pid}"),
            row("time", "Status", sc("Waiting for approval")),
            SEP,
            f"{E('bell')} {sc('You will get a message here as soon as it is approved.')}",
        ]),
        reply_markup=back_keyboard())


@dp.message(PayFlow.screenshot)
async def pay_need_photo(message: Message):
    await tidy(message)
    await notice(message.chat.id,
                 f"{E('warn')} {sc('Please send the screenshot as a photo, or /cancel to stop.')}", ttl=6)


async def _review(callback: CallbackQuery, approve: bool):
    if not await admin_only(callback):
        return
    pid = int(callback.data.split(":")[1])
    status = "approved" if approve else "rejected"
    with db() as con:
        # Only a still-pending payment can be reviewed (stops double clicks).
        done = con.execute(
            "UPDATE payments SET status=?, reviewed_at=? WHERE id=? AND status='pending'",
            (status, now().isoformat(), pid)).rowcount
        row_ = con.execute("SELECT * FROM payments WHERE id=?", (pid,)).fetchone()
    if not done or not row_:
        return await callback.answer("Already reviewed.", show_alert=True)

    uid = row_["user_id"]
    if approve:
        expiry = grant_premium(uid, row_["days"], extend=True)
        notified = await notify_premium(uid, expiry)
        verdict = f"{E('ok')} <b>{sc('Approved')}</b> — {sc('premium until')} {expiry:%Y-%m-%d}"
    else:
        notified = True
        try:
            await bot.send_message(
                uid,
                "\n".join([
                    bq(f"{E('err')} {sc('Payment rejected')}"),
                    "",
                    sc("Your payment could not be verified."),
                    f"{sc('If you think this is a mistake, contact')} @Noxmirage",
                ]),
                reply_markup=back_keyboard())
        except Exception:
            logging.exception("Could not notify %s about rejection", uid)
            notified = False
        verdict = f"{E('err')} <b>{sc('Rejected')}</b>"
    if not notified:
        verdict += f"\n{E('warn')} {sc('User could not be notified.')}"
    try:
        await callback.message.edit_caption(
            caption=f"{callback.message.html_text}\n\n{verdict}", reply_markup=None)
    except TelegramBadRequest:
        pass
    await callback.answer("Done.")


@dp.callback_query(F.data.startswith("payok:"))
async def pay_approve(callback: CallbackQuery):
    await _review(callback, approve=True)


@dp.callback_query(F.data.startswith("payno:"))
async def pay_reject(callback: CallbackQuery):
    await _review(callback, approve=False)


# ---- admin commands ---------------------------------------------------------
@dp.message(Command("premium"))
async def give_premium(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    parts = (message.text or "").split()
    if len(parts) != 3 or not parts[2].isdigit() or int(parts[2]) < 1:
        return await notice(chat, "Usage: <code>/premium @username days</code>\nExample: <code>/premium @example 30</code>", ttl=10)
    user = find_registered_user(parts[1])
    if not user:
        return await notice(chat, sc("Username not found. The user must start this bot first, and their username must be visible."), ttl=10)
    expiry = grant_premium(user["user_id"], int(parts[2]), extend=False)
    notified = await notify_premium(user["user_id"], expiry)
    name = esc(f"@{user['username']}" if user["username"] else user["user_id"])
    await notice(
        chat,
        f"{E('star')} {sc('Premium granted to')} {name} {sc('until')} {expiry.date()} ❗️\n"
        + (f"{E('bell')} {sc('User has been notified.')}" if notified
           else f"{E('warn')} {sc('Could not notify the user (they may have blocked the bot).')}"),
        ttl=15)


@dp.message(Command("removepremium"))
async def remove_premium(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    parts = (message.text or "").split()
    if len(parts) != 2:
        return await notice(chat, "Usage: <code>/removepremium @username</code>", ttl=10)
    user = find_registered_user(parts[1])
    if not user:
        return await notice(chat, sc("User not found. They must have started the bot."))
    with db() as con:
        con.execute("DELETE FROM premium WHERE user_id=?", (user["user_id"],))
    name = esc(f"@{user['username']}" if user["username"] else user["user_id"])
    await notice(chat, f"{E('ok')} {sc('Premium removed for')} {name}", ttl=10)


@dp.message(Command("setlimit"))
async def set_limit(message: Message):
    """/setlimit @username 50   -> custom daily limit for that user only
       /setlimit @username default -> back to the default limit"""
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    parts = (message.text or "").split()
    if len(parts) != 3 or not (parts[2].isdigit() or parts[2].lower() == "default"):
        return await notice(chat, "Usage: <code>/setlimit @username number</code>\n"
                                  "<code>/setlimit @username default</code>", ttl=10)
    user = find_registered_user(parts[1])
    if not user:
        return await notice(chat, sc("User not found. They must have started the bot."))
    value = None if parts[2].lower() == "default" else int(parts[2])
    with db() as con:
        con.execute("UPDATE users SET custom_limit=? WHERE user_id=?", (value, user["user_id"]))
    shown = f"{value}/day" if value is not None else f"default ({FREE_DAILY_LIMIT}/day)"
    name = esc(f"@{user['username']}" if user["username"] else user["user_id"])
    await notice(chat, f"{E('ok')} {sc('Daily limit for')} {name} ➛ {shown}", ttl=10)


@dp.message(Command("userinfo"))
async def user_info(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    parts = (message.text or "").split()
    if len(parts) != 2:
        return await notice(chat, "Usage: <code>/userinfo @username</code>", ttl=10)
    user = find_registered_user(parts[1])
    if not user:
        return await notice(chat, sc("User not found."))
    uid = user["user_id"]
    expiry = premium_expiry(uid)
    limit = user_daily_limit(uid)
    with db() as con:
        total = con.execute("SELECT COUNT(*) n FROM archives WHERE user_id=?", (uid,)).fetchone()["n"]
        conns = con.execute("SELECT COUNT(*) n FROM business_connections WHERE owner_id=?", (uid,)).fetchone()["n"]
    text = "\n".join([
        bq(f"{E('user')} @{esc(user['username'] or 'no_username')}"),
        "",
        row("id", "ID", f"<code>{uid}</code>"),
        row("ban", "Banned", sc(str(bool(user["banned"])))),
        row("star", "Premium", f"{expiry:%Y-%m-%d %H:%M} UTC" if expiry else sc("No")),
        row("bolt", "Daily Limit", "∞" if limit is None else limit),
        row("camera", "Used Today", daily_count(uid)),
        row("box", "Total Archived", total),
        row("link", "Connections", conns),
        row("time", "Last Seen", user["last_seen"][:16].replace("T", " ")),
    ])
    await notice(chat, text, ttl=90)  # long enough to read, then it cleans itself


@dp.message(Command("ban"))
async def ban_user(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    parts = (message.text or "").split()
    if len(parts) != 2:
        return await notice(chat, "Usage: <code>/ban @username</code>", ttl=10)
    user = find_registered_user(parts[1])
    if not user:
        return await notice(chat, sc("User not found."))
    with db() as con:
        con.execute("UPDATE users SET banned=1 WHERE user_id=?", (user["user_id"],))
    await notice(chat, f"{E('ban')} {sc('User banned')} ❗️", ttl=8)


@dp.message(Command("unban"))
async def unban_user(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    parts = (message.text or "").split()
    if len(parts) != 2:
        return await notice(chat, "Usage: <code>/unban @username</code>", ttl=10)
    user = find_registered_user(parts[1])
    if not user:
        return await notice(chat, sc("User not found."))
    with db() as con:
        con.execute("UPDATE users SET banned=0 WHERE user_id=?", (user["user_id"],))
    await notice(chat, f"{E('ok')} {sc('User unbanned.')}", ttl=8)


# ---- custom (animated) emoji setup -------------------------------------------------
@dp.message(Command("emojis"))
async def emojis_help(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await tidy(message)
    lines = [bq(f"{E('title')} {sc('Custom emojis')}"), ""]
    for name, fallback in EMOJI.items():
        mark = "✅" if name in EMOJI_IDS else "▫️"
        lines.append(f"{mark} {fallback} <code>{name}</code>")
    lines += [
        SEP,
        sc("To set one, send me a message with the name and a custom emoji, for example:"),
        "<code>ok</code> + [your custom emoji]",
        sc("Needs Telegram Premium on the bot owner's account."),
        "<code>/emojireset name</code> / <code>/emojireset all</code>",
    ]
    await notice(message.chat.id, "\n".join(lines), ttl=120)


@dp.message(Command("emojireset"))
async def emoji_reset(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await tidy(message)
    parts = (message.text or "").split()
    if len(parts) != 2:
        return await notice(message.chat.id, "Usage: <code>/emojireset name</code> or <code>all</code>", ttl=8)
    if parts[1] == "all":
        EMOJI_IDS.clear()
    else:
        EMOJI_IDS.pop(parts[1], None)
    save_emoji_ids()
    await notice(message.chat.id, f"{E('ok')} {sc('Done.')}", ttl=5)


def _has_custom_emoji(m: Message):
    return bool(m.from_user and m.from_user.id == OWNER_ID and m.text and
                any(e.type == "custom_emoji" for e in (m.entities or [])))


@dp.message(_has_custom_emoji)
async def emoji_setter(message: Message):
    """Owner sends '<name> <custom emoji>' -> that emoji replaces <name> everywhere."""
    ids = [e.custom_emoji_id for e in message.entities if e.type == "custom_emoji"]
    name = message.text.split()[0].lower()
    await tidy(message)
    if name not in EMOJI:
        return await notice(
            message.chat.id,
            f"{E('warn')} {sc('Start your message with a name from /emojis. The ID of your emoji is:')} "
            f"<code>{ids[0]}</code>", ttl=20)
    EMOJI_IDS[name] = ids[0]
    save_emoji_ids()
    await notice(message.chat.id, f"{E('ok')} <code>{name}</code> {sc('is now')} {E(name)}", ttl=8)


# ----------------------------------------------------------------------------
# Archiving (Business messages)
# ----------------------------------------------------------------------------
def archive_caption():
    return "\n".join([
        bq(f"{E('ok')} <b>{sc('Media successfully archived')}</b> ❗️"),
        row("bot", "Bot", "@ErebusSpyBot"),
        row("dev", "Developer", "@Noxmirage"),
    ])


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
    cap = dict(caption=archive_caption())
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
    #    message once per connection -- this check stops the double handling.)
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
        await notice(owner_id, f"{E('err')} {sc('No supported media found in the replied message.')}", ttl=10)
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
        await notice(
            owner_id,
            "\n".join([
                bq(f"{E('warn')} {sc('Daily limit reached')}"),
                row("bolt", "Limit", f"{limit} {sc('per day')}"),
                row("time", "Resets", "00:00 UTC"),
                SEP,
                f"{E('star')} {sc('Go premium for unlimited archives.')}",
            ]),
            ttl=120, reply_markup=back_keyboard([btn("Buy Premium", "pay", style="primary", callback_data="buy")]))
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
        await notice(owner_id, f"{E('err')} {sc('Media retrieval failed. Please try again.')}", ttl=10)


# ----------------------------------------------------------------------------
async def main():
    init_db()
    load_emoji_ids()
    if not HAS_ICON:
        print("Note: aiogram < 3.25 -> button icons/colours disabled (pip install -U aiogram)")
    print("Media Archive Bot Started Successfully!")
    print("Bot is running...")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "business_connection", "business_message", "callback_query"]
    )


if __name__ == "__main__":
    asyncio.run(main())
