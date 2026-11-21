import asyncio
import contextvars
import csv
import html
import itertools
import json
import logging
import os
import random
import re
import sqlite3
import tempfile
import time
import uuid
from collections import deque
from contextlib import contextmanager
#Only for tha flask and web if removing web then remove import too and from requirements.txt the module instalation
from flask import Flask, send_from_directory
from threading import Thread

from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
from pathlib import Path
from urllib.parse import quote

import aiohttp
from dotenv import load_dotenv
from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, BufferedInputFile, BusinessConnection, BusinessMessagesDeleted,
)

try:
    import qrcode  # pip install "qrcode[pil]"
except ImportError:
    qrcode = None

#For Render 24/7 host a web page 

app = Flask(__name__)
@app.route("/")
def home():
    return send_from_directory(
        os.path.dirname(os.path.abspath(__file__)),
        "index.html"
    )

@app.route("/health")
def health():
    return "OK", 200

def run():
    app.run(host="0.0.0.0", port=10000)

def keep_alive():
    server = Thread(target=run, daemon=True)
    server.start()

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

# ---- Media storage ------------------------------------------------------------
# SAVE_MEDIA_LOCALLY = False (default): archived files are downloaded only as TEMPORARY files,
#     handed to the owner + copied to the Telegram backup, then deleted from the server at once.
# SAVE_MEDIA_LOCALLY = True: every archived file is also kept in ./saved_media on the server.
# (You can also set SAVE_MEDIA_LOCALLY=1 in .env instead of editing the line below.)
SAVE_MEDIA_LOCALLY = os.getenv("SAVE_MEDIA_LOCALLY", "0") == "1"
# Telegram backup of archived media: a copy goes to MEDIA_CHAT_ID (see below). Default ON.
# The database backup (BACKUP_CHAT_ID) is separate and stays ON as before.
TG_MEDIA_BACKUP = os.getenv("TG_MEDIA_BACKUP", "1") == "1"
SAVE_DIR = Path("saved_media")
TEMP_DIR = Path(tempfile.gettempdir()) / "erebus_tmp_media"
if SAVE_MEDIA_LOCALLY:
    SAVE_DIR.mkdir(exist_ok=True)
TRIGGER_WORD = "shit"
MAX_TRIGGERS = 3                                     # custom archive commands per user (premium)
TRIGGER_TRIAL_DAYS = int(os.getenv("TRIGGER_TRIAL_DAYS", "7"))

TUTORIAL_URL = "https://youtube.com/shorts/KRKAUY3g7wk?si=G5eeDrXGJ5ZGfX5v"

# Deleted-message saver (premium feature) and the free trial every user gets
TRIAL_DAYS = 3
DELETED_CACHE_HOURS = int(os.getenv("DELETED_CACHE_HOURS", "72"))   # how long copies are kept
ANNOUNCE_TRIAL_ON_START = os.getenv("ANNOUNCE_TRIAL_ON_START", "1") == "1"

# Payments (UPI). PLANS = {plan: (days of premium, price in INR)}
UPI_ID = os.getenv("UPI_ID", "Ahmadammar@fam")
UPI_NAME = os.getenv("UPI_NAME", "Erebus Premium")
PLANS = {1: (7, 29), 2: (14, 39), 3: (30, 49)}
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


_LANG = contextvars.ContextVar("lang", default="en")   # language of the person being answered
HI = {}                                                    # English text -> Hindi (filled below)
HL = {}                                                    # English text -> Hinglish (filled below)


def sc(text):
    """Bot font: 'free plan' -> 'Fʀᴇᴇ Pʟᴀɴ'. First letter of each word is a capital;
    ACRONYMS, numbers, @names and /commands stay untouched. Users who chose Hindi get the
    Hindi text from HI when there is one."""
    lang = _LANG.get()
    if lang == "hi":
        translated = HI.get(text)
        if translated is not None:
            return translated
    elif lang == "hl":                       # Hinglish keeps the same bot font
        text = HL.get(text, text)
    def word(w):
        if any(c.isdigit() or c in "@/₹_<>&" for c in w) or (w.isupper() and len(w) > 1):
            return w
        m = re.search(r"[A-Za-z]", w)
        if not m:
            return w
        i = m.end()
        return w[:i - 1] + w[i - 1].upper() + w[i:].translate(_SMALL_CAPS)
    return "".join(p if p.isspace() else word(p) for p in re.split(r"(\s+)", text))


# ---- Hindi texts (users pick the language with the Language button) -------------------
# Key = the exact English text passed to sc(); anything missing simply stays English.
HI.update({
    # main menu and screens
    "Welcome": "स्वागत है", "Media Archive Bot": "मीडिया आर्काइव बॉट", "Plan": "प्लान", "Premium": "प्रीमियम",
    "Free": "फ्री", "Today": "आज", "Deleted Saver": "डिलीट सेवर",
    "Reply to any media with": "किसी भी मीडिया को रिप्लाई करके लिखें",
    "How To Archive": "आर्काइव कैसे करें", "My Premium": "मेरा प्रीमियम", "My Statistics": "मेरे आँकड़े",
    "Membership": "मेंबरशिप", "Buy Premium": "प्रीमियम खरीदें", "Repeated Message": "रिपीट मैसेज",
    "Deleted Messages": "डिलीट मैसेज", "Auto Reply": "ऑटो रिप्लाई", "Tools": "टूल्स", "Language": "भाषा",
    "Updates": "अपडेट्स", "Main Menu": "मुख्य मेनू", "Back": "वापस", "Cancelled.": "रद्द किया गया।",
    "Send /cancel to stop.": "रोकने के लिए /cancel भेजें।", "Example:": "उदाहरण:",
    "Access locked": "एक्सेस बंद है", "Join Channel": "चैनल जॉइन करें", "Join Group": "ग्रुप जॉइन करें",
    "I've Joined": "मैंने जॉइन कर लिया", "Join our channel and group, then tap I've Joined.":
        "हमारा चैनल और ग्रुप जॉइन करें, फिर \"मैंने जॉइन कर लिया\" दबाएँ।",
    # how to archive
    "How to archive media": "मीडिया आर्काइव कैसे करें", "Step 1": "स्टेप 1", "Step 2": "स्टेप 2",
    "Step 3": "स्टेप 3", "Step 4": "स्टेप 4", "Start this bot": "इस बॉट को स्टार्ट करें",
    "Settings → Business → Chatbots → add this bot": "सेटिंग्स → बिज़नेस → चैटबॉट्स → इस बॉट को जोड़ें",
    "In any chat, reply to a media with": "किसी भी चैट में मीडिया को रिप्लाई करके लिखें",
    "The copy arrives here, in your private chat": "कॉपी यहीं, आपकी प्राइवेट चैट में आ जाएगी",
    "Only you (the account owner) can trigger it.": "इसे सिर्फ़ आप (अकाउंट मालिक) चला सकते हैं।",
    "Watch the video tutorial": "वीडियो ट्यूटोरियल देखें", "Watch Tutorial": "ट्यूटोरियल देखें",
    "A backup copy is also kept in our private archive.": "एक बैकअप कॉपी हमारे प्राइवेट आर्काइव में भी रखी जाती है।",
    "No copy is stored on our server. You can only view your saved photos here in the bot.":
        "हमारे सर्वर पर कोई कॉपी सेव नहीं होती। आप अपनी सेव की हुई फ़ोटो सिर्फ़ यहीं बॉट में देख सकते हैं।",
    "A private recovery copy is kept in the bot owner Telegram archive.":
        "रिकवरी के लिए एक प्राइवेट कॉपी बॉट मालिक के टेलीग्राम आर्काइव में रखी जाती है।",
    "No backup is saved, for your privacy. You can only view your saved photos here in the bot.":
        "आपकी प्राइवेसी के लिए कोई बैकअप सेव नहीं किया जाता। आप अपनी सेव की हुई फ़ोटो सिर्फ़ यहीं बॉट में देख सकते हैं।",
    # premium / stats
    "Premium member": "प्रीमियम मेंबर", "Active Until": "सक्रिय है", "Daily Limit": "रोज़ की सीमा",
    "Unlimited": "अनलिमिटेड", "Used Today": "आज इस्तेमाल", "You are on the free plan": "आप फ्री प्लान पर हैं",
    "Go premium for unlimited archives.": "अनलिमिटेड आर्काइव के लिए प्रीमियम लें।", "Extend Premium": "प्रीमियम बढ़ाएँ",
    "Your statistics": "आपके आँकड़े", "Total Archived": "कुल आर्काइव", "Premium Activated": "प्रीमियम चालू हो गया",
    "Congratulations!": "बधाई हो!", "Valid Till": "वैध है", "Daily limit reached": "आज की सीमा पूरी हो गई",
    "Limit": "सीमा", "Resets": "रीसेट", "per day": "प्रति दिन", "Premium plans": "प्रीमियम प्लान",
    "Unlimited archives on every plan.": "हर प्लान में अनलिमिटेड आर्काइव।",
    "Select a plan to get your payment QR.": "पेमेंट QR पाने के लिए प्लान चुनें।",
    "Amount": "रकम", "Validity": "वैधता", "days": "दिन", "Back To Plans": "प्लान पर वापस",
    "I've Paid — Send Screenshot": "मैंने पेमेंट कर दिया — स्क्रीनशॉट भेजें",
    "Scan this QR with any UPI app": "इस QR को किसी भी UPI ऐप से स्कैन करें",
    "Pay the exact amount": "सही रकम का भुगतान करें",
    "Tap the button below and send the screenshot": "नीचे का बटन दबाकर स्क्रीनशॉट भेजें",
    "Send your payment screenshot now as a photo.": "अब अपने पेमेंट का स्क्रीनशॉट फ़ोटो के रूप में भेजें।",
    "Screenshot received": "स्क्रीनशॉट मिल गया", "Payment": "पेमेंट", "Status": "स्थिति",
    "Waiting for approval": "मंज़ूरी का इंतज़ार",
    "You will get a message here as soon as it is approved.": "मंज़ूरी मिलते ही आपको यहीं मैसेज मिलेगा।",
    "Payment rejected": "पेमेंट अस्वीकार हुआ", "Your payment could not be verified.": "आपका पेमेंट वेरिफ़ाई नहीं हो सका।",
    "If you think this is a mistake, contact": "अगर आपको लगता है कि यह गलती है, तो संपर्क करें",
    "Payment cancelled.": "पेमेंट रद्द किया गया।",
    # deleted messages + trial
    "Deleted message saver": "डिलीट मैसेज सेवर", "Access": "एक्सेस", "Saving": "सेविंग", "On": "चालू", "Off": "बंद",
    "Kept For": "रखा जाता है", "hours": "घंटे", "Active": "चालू", "Locked": "लॉक्ड", "Free Trial": "फ्री ट्रायल",
    "When someone deletes a message in your private chats, the bot sends it to you here.":
        "जब कोई आपकी प्राइवेट चैट में मैसेज डिलीट करता है, तो बॉट उसे आपको यहीं भेज देता है।",
    "Text, photos, videos, voice notes, stickers and files are saved.":
        "टेक्स्ट, फ़ोटो, वीडियो, वॉइस नोट, स्टिकर और फ़ाइलें सेव होती हैं।",
    "Needs this bot connected in Settings → Business → Chatbots.":
        "इसके लिए बॉट सेटिंग्स → बिज़नेस → चैटबॉट्स में जुड़ा होना चाहिए।",
    "Your free trial has ended. Premium unlocks this again.": "आपका फ्री ट्रायल खत्म हो गया। प्रीमियम से यह फिर चालू होगा।",
    "Your free trial has ended. Premium saves deleted messages for you.":
        "आपका फ्री ट्रायल खत्म हो गया। प्रीमियम आपके लिए डिलीट मैसेज सेव करता है।",
    "A message was deleted in one of your chats": "आपकी किसी चैट में एक मैसेज डिलीट हुआ",
    "Turn Off": "बंद करें", "Turn On": "चालू करें", "Deleted message": "डिलीट किया गया मैसेज", "From": "किसने", "Sent": "भेजा",
    "New: deleted message saver": "नया: डिलीट मैसेज सेवर", "Free trial unlocked": "फ्री ट्रायल चालू हो गया",
    "Trial Ends": "ट्रायल खत्म", "Keep it after the trial with Premium.": "ट्रायल के बाद भी रखने के लिए प्रीमियम लें।",
    "It is included in your Premium.": "यह आपके प्रीमियम में शामिल है।",
    "Connect this bot in Settings → Business → Chatbots.": "इस बॉट को सेटिंग्स → बिज़नेस → चैटबॉट्स में जोड़ें।",
    # repeater
    "Repeated message": "रिपीट मैसेज", "Messages": "मैसेज", "Interval": "अंतराल", "Count": "गिनती", "Presets": "प्रीसेट",
    "In any chat send:": "किसी भी चैट में भेजें:", "Set Messages": "मैसेज सेट करें", "Add Media": "मीडिया जोड़ें",
    "Clear Items": "सब हटाएँ", "Save Current As Preset": "मौजूदा को प्रीसेट में सेव करें",
    "Send your messages separated by": "अपने मैसेज भेजें, इनके बीच में लगाएँ",
    "This replaces the whole list.": "इससे पूरी लिस्ट बदल जाएगी।",
    "Send a photo, sticker, voice note, video or file to add it to the cycle.":
        "साइकल में जोड़ने के लिए फ़ोटो, स्टिकर, वॉइस नोट, वीडियो या फ़ाइल भेजें।",
    "Send the interval in seconds, minimum": "सेकंड में अंतराल भेजें, कम से कम",
    "Send how many messages to send, example": "कितने मैसेज भेजने हैं, उदाहरण",
    "Please send some text.": "कृपया कुछ टेक्स्ट भेजें।", "Send a whole number, like 20": "पूरी संख्या भेजें, जैसे 20",
    "Send a number like 0.5 or a range like 0.5-2": "0.5 जैसी संख्या या 0.5-2 जैसी रेंज भेजें",
    "Repeater": "रिपीटर", "Running": "चल रहा है", "Paused": "रुका हुआ", "Finished": "पूरा हुआ", "Stopped": "रोका गया",
    "Failed": "फ़ेल", "Chat": "चैट", "Pause": "रोकें", "Resume": "फिर शुरू करें", "Stop": "बंद करें",
    "This repeater has already ended.": "यह रिपीटर पहले ही खत्म हो चुका है।", "Stopping...": "रोक रहे हैं...",
    "No messages set. Open Repeated Message in the bot menu.": "कोई मैसेज सेट नहीं है। बॉट मेनू में रिपीट मैसेज खोलें।",
    "Already running there. Send .Axstop first.": "वहाँ पहले से चल रहा है। पहले .Axstop भेजें।",
    "Nothing is running in that chat.": "उस चैट में कुछ नहीं चल रहा।",
    "Repeater presets": "रिपीटर प्रीसेट", "Add some messages first.": "पहले कुछ मैसेज जोड़ें।",
    "A preset stores your current messages, interval and count.": "प्रीसेट आपके मौजूदा मैसेज, अंतराल और गिनती को सेव रखता है।",
    "No presets yet. Set up your messages, then save them here.": "अभी कोई प्रीसेट नहीं। मैसेज सेट करें, फिर यहाँ सेव करें।",
    "items": "आइटम",
    # auto reply
    "Auto reply (away mode)": "ऑटो रिप्लाई (अवे मोड)", "Replying": "जवाब दे रहा है", "Manual": "मैन्युअल",
    "Schedule": "शेड्यूल", "Timezone": "टाइमज़ोन", "Message": "मैसेज", "not set": "सेट नहीं",
    "on": "चालू", "off": "बंद", "Turn Off Now": "अभी बंद करें", "Turn On Now": "अभी चालू करें",
    "Set Message": "मैसेज सेट करें",
    "People who message you get this reply once every 6 hours.": "आपको मैसेज करने वालों को हर 6 घंटे में एक बार यह जवाब मिलता है।",
    "In a chat:": "किसी चैट में:", "(only for that chat)": "(सिर्फ़ उस चैट के लिए)",
    "Send your away message.": "अपना अवे मैसेज भेजें।",
    "Send the daily away hours in your timezone.": "अपने टाइमज़ोन में रोज़ के अवे घंटे भेजें।",
    "Send your timezone offset from UTC.": "UTC से अपने टाइमज़ोन का अंतर भेजें।",
    "Use the format 22:00-07:00": "22:00-07:00 फ़ॉर्मैट में भेजें", "Use an offset like +5:30 or -4": "+5:30 या -4 जैसा अंतर भेजें",
    "Away mode is on": "अवे मोड चालू", "Away mode is off.": "अवे मोड बंद।", "until": "तक",
    "This chat uses your default away message.": "यह चैट आपका डिफ़ॉल्ट अवे मैसेज इस्तेमाल करेगी।",
    "Away message for": "अवे मैसेज इसके लिए",
    # tools
    "Everything here works inside your own chats through your Business account.":
        "यहाँ सब कुछ आपके बिज़नेस अकाउंट से आपकी अपनी चैट्स में काम करता है।",
    "Keyword Replies": "कीवर्ड रिप्लाई", "Keyword replies": "कीवर्ड रिप्लाई", "Snippets": "स्निपेट्स",
    "Scheduled": "शेड्यूल्ड", "Activity": "गतिविधि", "Commands": "कमांड्स", "Panic": "पैनिक",
    "Commands for your chats": "आपकी चैट्स के कमांड्स",
    "Only you can use these, and the bot deletes the command message.": "इन्हें सिर्फ़ आप चला सकते हैं, और बॉट कमांड वाला मैसेज हटा देता है।",
    "Add Rule": "रूल जोड़ें", "Add Snippet": "स्निपेट जोड़ें", "No keyword rules yet.": "अभी कोई कीवर्ड रूल नहीं।",
    "No snippets yet.": "अभी कोई स्निपेट नहीं।",
    "When someone writes a keyword in a private chat, your account answers for you.":
        "जब कोई प्राइवेट चैट में कीवर्ड लिखता है, तो आपका अकाउंट आपकी तरफ़ से जवाब देता है।",
    "Type .name in any chat and your message turns into the saved text.":
        "किसी भी चैट में .नाम लिखें और आपका मैसेज सेव किए गए टेक्स्ट में बदल जाएगा।",
    "Send the keyword and the reply, separated by": "कीवर्ड और जवाब भेजें, इनके बीच में लगाएँ",
    "Send the name and the text, separated by": "नाम और टेक्स्ट भेजें, इनके बीच में लगाएँ",
    "Use the format: keyword | reply": "इस फ़ॉर्मैट में भेजें: कीवर्ड | जवाब",
    "Use the format: name | text": "इस फ़ॉर्मैट में भेजें: नाम | टेक्स्ट",
    "Keyword saved": "कीवर्ड सेव हुआ", "Removed": "हटाया गया", "Not found": "नहीं मिला",
    "Snippet saved. Use it with": "स्निपेट सेव हुआ। इससे इस्तेमाल करें",
    "Scheduled messages": "शेड्यूल्ड मैसेज", "Nothing scheduled.": "कुछ शेड्यूल नहीं है।",
    "Recent activity": "हाल की गतिविधि", "Nothing yet.": "अभी कुछ नहीं।", "Scheduled for": "शेड्यूल किया गया",
    "Panic done": "पैनिक पूरा", "Repeaters Stopped": "रिपीटर बंद", "Shh Chats Off": "शह चैट बंद",
    "Scheduled Cleared": "शेड्यूल्ड हटाए", "Away Mode": "अवे मोड",
    "Saved message": "सेव किया गया मैसेज", "Reply to a message with .save": "किसी मैसेज को रिप्लाई करके .save लिखें",
    "Could not save that message.": "वह मैसेज सेव नहीं हो सका।", "Translation": "अनुवाद",
    "Translation failed, try again.": "अनुवाद नहीं हो सका, दोबारा कोशिश करें।",
    "Shh is now": "शह अब", "ON": "चालू", "Shh ended": "शह खत्म", "Shh log": "शह लॉग", "Ends": "खत्म",
    "Allowed Words": "अनुमत शब्द", "Allowed Media": "अनुमत मीडिया", "Log": "लॉग", "none": "कोई नहीं", "never": "कभी नहीं",
    "Blocked words": "ब्लॉक किए शब्द", "Link filter": "लिंक फ़िल्टर", "Flood filter": "फ़्लड फ़िल्टर",
    "Choose your language.": "अपनी भाषा चुनें।", "Done.": "हो गया।",
    "Media successfully archived": "मीडिया सफलतापूर्वक आर्काइव हुआ", "Media retrieval failed. Please try again.":
        "मीडिया नहीं मिल सका। कृपया दोबारा कोशिश करें।",
    "No supported media found in the replied message.": "रिप्लाई किए गए मैसेज में कोई सपोर्टेड मीडिया नहीं मिला।",
    "Your access has been disabled.": "आपका एक्सेस बंद कर दिया गया है।",
    # custom commands + calls
    "Trigger Words": "ट्रिगर शब्द", "Custom commands": "कस्टम कमांड", "Custom command": "कस्टम कमांड",
    "Call history": "कॉल हिस्ट्री", "Call": "कॉल", "Type": "टाइप", "Duration": "अवधि",
    "Default": "डिफ़ॉल्ट", "Custom": "कस्टम", "Slots": "स्लॉट", "Add Command": "कमांड जोड़ें", "Remove": "हटाएँ",
    "Send the word to archive with.": "आर्काइव करने के लिए शब्द भेजें।",
    "Letters, numbers or _ only.": "सिर्फ़ अक्षर, संख्या या _ भेजें।",
    "Letters, numbers or _ only, max 24 characters.": "सिर्फ़ अक्षर, संख्या या _, ज़्यादा से ज़्यादा 24 अक्षर।",
    "That is already the default word, pick another.": "यह पहले से डिफ़ॉल्ट शब्द है, दूसरा चुनें।",
    "Reply to any media with one of these words and the copy arrives here.":
        "किसी भी मीडिया को इनमें से किसी शब्द से रिप्लाई करें और कॉपी यहाँ आ जाएगी।",
    "The default word always works, custom ones are a premium feature.":
        "डिफ़ॉल्ट शब्द हमेशा काम करता है, कस्टम शब्द प्रीमियम फ़ीचर है।",
    "Your 7 day trial has ended. Premium unlocks custom commands.":
        "आपका 7 दिन का ट्रायल खत्म हो गया। प्रीमियम से कस्टम कमांड चालू होगा।",
    "Your trial has ended. Premium unlocks custom commands.":
        "आपका ट्रायल खत्म हो गया। प्रीमियम से कस्टम कमांड चालू होगा।",
    "New: custom archive commands": "नया: कस्टम आर्काइव कमांड",
    "You can archive media with your own words now, up to": "अब आप अपने शब्दों से मीडिया आर्काइव कर सकते हैं, कुल",
    "Reply to any media with your word, exactly like the default one.":
        "किसी भी मीडिया को अपने शब्द से रिप्लाई करें, बिल्कुल डिफ़ॉल्ट शब्द की तरह।",
    "7 day free trial unlocked": "7 दिन का फ्री ट्रायल चालू हो गया",
    "Every call in your connected chats is recorded here automatically.":
        "आपकी जुड़ी हुई चैट्स की हर कॉल यहाँ अपने आप रिकॉर्ड होती है।",
    "Call history is off for you.": "आपके लिए कॉल हिस्ट्री बंद है।",
})


# ---- new texts (edited messages, contact admin, privacy) in Hindi ----------------------
HI.update({
    "Contact Admin": "एडमिन से संपर्क करें", "Reply": "जवाब दें", "You are the admin.": "आप ही एडमिन हैं।",
    "Write your message for the admin. You can send text, a photo, voice note or file.":
        "एडमिन के लिए अपना मैसेज लिखें। आप टेक्स्ट, फ़ोटो, वॉइस नोट या फ़ाइल भेज सकते हैं।",
    "Please wait a few seconds before sending another message.": "दूसरा मैसेज भेजने से पहले कुछ सेकंड रुकें।",
    "Sent to the admin. The reply will arrive here.": "एडमिन को भेज दिया गया। जवाब यहीं आएगा।",
    "Message from the admin": "एडमिन का मैसेज",
    "That type is not supported. Send text, a photo, sticker, voice, video or file.":
        "यह टाइप सपोर्टेड नहीं है। टेक्स्ट, फ़ोटो, स्टिकर, वॉइस, वीडियो या फ़ाइल भेजें।",
    "Edited message": "एडिट किया गया मैसेज", "Before": "पहले", "After": "बाद में", "Original": "ओरिजिनल", "Now": "अब",
    "It also tells you when they edit a message, with the old and the new text.":
        "जब कोई मैसेज एडिट करता है, तो यह पुराना और नया टेक्स्ट दोनों भी भेजता है।",
    "For privacy, nothing is stored on our server and only you can see your saved photos and media here in your bot. A private recovery copy is kept in the bot owner Telegram archive.":
        "प्राइवेसी के लिए हमारे सर्वर पर कुछ सेव नहीं होता और आपकी सेव की हुई फ़ोटो और मीडिया सिर्फ़ आप ही यहाँ अपने बॉट में देख सकते हैं। रिकवरी के लिए एक प्राइवेट कॉपी बॉट मालिक के टेलीग्राम आर्काइव में रखी जाती है।",
    "For privacy reasons no backup is stored. Only you can see your photos and media here in your bot.":
        "प्राइवेसी के कारण कोई बैकअप सेव नहीं किया जाता। आपकी फ़ोटो और मीडिया सिर्फ़ आप ही यहाँ अपने बॉट में देख सकते हैं।",
})

# ---- Hinglish (Hindi written in English letters) ------------------------------------------
HL.update({
    "Welcome": "Swagat hai", "Media Archive Bot": "Media Archive Bot", "Plan": "Plan", "Premium": "Premium",
    "Free": "Free", "Today": "Aaj", "Deleted Saver": "Delete Saver",
    "Reply to any media with": "Kisi bhi media ko reply karke likho",
    "How To Archive": "Archive Kaise Kare", "My Premium": "Mera Premium", "My Statistics": "Mere Stats",
    "Membership": "Membership", "Buy Premium": "Premium Kharido", "Repeated Message": "Repeat Message",
    "Deleted Messages": "Delete Messages", "Auto Reply": "Auto Reply", "Tools": "Tools", "Language": "Language",
    "Updates": "Updates", "Main Menu": "Main Menu", "Back": "Wapas", "Cancelled.": "Cancel ho gaya.",
    "Send /cancel to stop.": "Rokne ke liye /cancel bhejo.", "Example:": "Example:",
    "Access locked": "Access band hai", "Join Channel": "Channel Join Karo", "Join Group": "Group Join Karo",
    "I've Joined": "Maine Join Kar Liya",
    "Join our channel and group, then tap I've Joined.": "Hamara channel aur group join karo, phir I've Joined dabao.",
    "How to archive media": "Media archive kaise kare", "Step 1": "Step 1", "Step 2": "Step 2", "Step 3": "Step 3", "Step 4": "Step 4",
    "Start this bot": "Is bot ko start karo",
    "Settings → Business → Chatbots → add this bot": "Settings → Business → Chatbots → is bot ko add karo",
    "In any chat, reply to a media with": "Kisi bhi chat me media ko reply karke likho",
    "The copy arrives here, in your private chat": "Copy yahin, tumhari private chat me aa jayegi",
    "Watch the video tutorial": "Video tutorial dekho", "Watch Tutorial": "Tutorial Dekho",
    "A backup copy is also kept in our private archive.": "Ek backup copy hamare private archive me bhi rakhi jati hai.",
    "For privacy, nothing is stored on our server and only you can see your saved photos and media here in your bot. A private recovery copy is kept in the bot owner Telegram archive.":
        "Privacy ke liye hamare server par kuch save nahi hota aur tumhari saved photos aur media sirf tum hi yahan apne bot me dekh sakte ho. Recovery ke liye ek private copy bot owner ke Telegram archive me rakhi jati hai.",
    "For privacy reasons no backup is stored. Only you can see your photos and media here in your bot.":
        "Privacy ki wajah se koi backup save nahi hota. Tumhari photos aur media sirf tum hi yahan apne bot me dekh sakte ho.",
    "Premium member": "Premium member", "Active Until": "Active Hai", "Daily Limit": "Daily Limit", "Unlimited": "Unlimited",
    "Used Today": "Aaj Use Kiya", "You are on the free plan": "Tum free plan par ho",
    "Go premium for unlimited archives.": "Unlimited archive ke liye premium lo.", "Extend Premium": "Premium Badhao",
    "Your statistics": "Tumhare stats", "Total Archived": "Total Archive", "Premium Activated": "Premium Chalu Ho Gaya",
    "Congratulations!": "Badhai ho!", "Valid Till": "Valid Till", "Daily limit reached": "Aaj ki limit poori ho gayi",
    "Limit": "Limit", "Resets": "Reset", "per day": "per din", "Premium plans": "Premium plans",
    "Unlimited archives on every plan.": "Har plan me unlimited archive.",
    "Select a plan to get your payment QR.": "Payment QR ke liye plan chuno.",
    "Amount": "Amount", "Validity": "Validity", "days": "din", "Back To Plans": "Plans Par Wapas",
    "I've Paid — Send Screenshot": "Maine Pay Kar Diya — Screenshot Bhejo",
    "Scan this QR with any UPI app": "Is QR ko kisi bhi UPI app se scan karo",
    "Pay the exact amount": "Bilkul sahi amount pay karo",
    "Tap the button below and send the screenshot": "Neeche button dabao aur screenshot bhejo",
    "Send your payment screenshot now as a photo.": "Ab apna payment screenshot photo ki tarah bhejo.",
    "Screenshot received": "Screenshot mil gaya", "Payment": "Payment", "Status": "Status",
    "Waiting for approval": "Approval ka wait",
    "You will get a message here as soon as it is approved.": "Approve hote hi tumhe yahin message milega.",
    "Payment rejected": "Payment reject hua", "Your payment could not be verified.": "Tumhara payment verify nahi ho paya.",
    "If you think this is a mistake, contact": "Agar lagta hai ye galti hai, to contact karo",
    "Payment cancelled.": "Payment cancel ho gaya.",
    "Deleted message saver": "Delete Message Saver", "Access": "Access", "Saving": "Saving", "On": "On", "Off": "Off",
    "Kept For": "Kitne Time Tak", "hours": "ghante", "Active": "Active", "Locked": "Locked", "Free Trial": "Free Trial",
    "When someone deletes a message in your private chats, the bot sends it to you here.":
        "Jab koi tumhari private chat me message delete karta hai, bot use tumhe yahin bhej deta hai.",
    "It also tells you when they edit a message, with the old and the new text.":
        "Jab koi message edit karta hai to bot purana aur naya text dono bhi bhejta hai.",
    "Text, photos, videos, voice notes, stickers and files are saved.":
        "Text, photos, videos, voice notes, stickers aur files save hote hain.",
    "Needs this bot connected in Settings → Business → Chatbots.":
        "Iske liye bot Settings → Business → Chatbots me connected hona chahiye.",
    "Your free trial has ended. Premium unlocks this again.": "Tumhara free trial khatam ho gaya. Premium se ye phir chalu hoga.",
    "Your free trial has ended. Premium saves deleted messages for you.":
        "Tumhara free trial khatam ho gaya. Premium tumhare liye deleted messages save karta hai.",
    "A message was deleted in one of your chats": "Tumhari ek chat me message delete hua",
    "Turn Off": "Band Karo", "Turn On": "Chalu Karo", "Deleted message": "Deleted message", "From": "Kisne", "Sent": "Bheja",
    "Edited message": "Edited message", "Before": "Pehle", "After": "Baad me", "Original": "Original", "Now": "Ab",
    "New: deleted message saver": "Naya: Delete Message Saver", "Free trial unlocked": "Free trial chalu ho gaya",
    "Trial Ends": "Trial Khatam", "Keep it after the trial with Premium.": "Trial ke baad rakhne ke liye Premium lo.",
    "It is included in your Premium.": "Ye tumhare Premium me shamil hai.",
    "Connect this bot in Settings → Business → Chatbots.": "Is bot ko Settings → Business → Chatbots me connect karo.",
    "Repeated message": "Repeat Message", "Messages": "Messages", "Interval": "Interval", "Count": "Count", "Presets": "Presets",
    "In any chat send:": "Kisi bhi chat me bhejo:", "Set Messages": "Messages Set Karo", "Add Media": "Media Jodo",
    "Clear Items": "Sab Hatao", "Save Current As Preset": "Abhi Wale Ko Preset Me Save Karo",
    "Send your messages separated by": "Apne messages bhejo, beech me ye lagao",
    "This replaces the whole list.": "Isse poori list badal jayegi.",
    "Send a photo, sticker, voice note, video or file to add it to the cycle.":
        "Cycle me jodne ke liye photo, sticker, voice note, video ya file bhejo.",
    "Send the interval in seconds, minimum": "Seconds me interval bhejo, kam se kam",
    "Send how many messages to send, example": "Kitne messages bhejne hain, example",
    "Please send some text.": "Please kuch text bhejo.", "Send a whole number, like 20": "Poora number bhejo, jaise 20",
    "Send a number like 0.5 or a range like 0.5-2": "0.5 jaisa number ya 0.5-2 jaisi range bhejo",
    "Repeater": "Repeater", "Running": "Chal Raha Hai", "Paused": "Ruka Hua", "Finished": "Poora Hua", "Stopped": "Roka Gaya",
    "Failed": "Fail", "Chat": "Chat", "Pause": "Roko", "Resume": "Phir Shuru Karo", "Stop": "Band Karo",
    "This repeater has already ended.": "Ye repeater pehle hi khatam ho chuka hai.", "Stopping...": "Rok rahe hain...",
    "No messages set. Open Repeated Message in the bot menu.": "Koi message set nahi hai. Bot menu me Repeat Message kholo.",
    "Already running there. Send .Axstop first.": "Wahan pehle se chal raha hai. Pehle .Axstop bhejo.",
    "Nothing is running in that chat.": "Us chat me kuch nahi chal raha.",
    "Repeater presets": "Repeater Presets", "Add some messages first.": "Pehle kuch messages jodo.",
    "A preset stores your current messages, interval and count.": "Preset tumhare abhi ke messages, interval aur count save rakhta hai.",
    "No presets yet. Set up your messages, then save them here.": "Abhi koi preset nahi. Messages set karo, phir yahan save karo.",
    "items": "items",
    "Auto reply (away mode)": "Auto Reply (Away Mode)", "Replying": "Reply De Raha Hai", "Manual": "Manual",
    "Schedule": "Schedule", "Timezone": "Timezone", "Message": "Message", "not set": "set nahi",
    "on": "on", "off": "off", "Turn Off Now": "Abhi Band Karo", "Turn On Now": "Abhi Chalu Karo",
    "Set Message": "Message Set Karo",
    "People who message you get this reply once every 6 hours.": "Jo log tumhe message karte hain unhe ye reply 6 ghante me ek baar milta hai.",
    "In a chat:": "Kisi chat me:", "(only for that chat)": "(sirf us chat ke liye)",
    "Send your away message.": "Apna away message bhejo.",
    "Send the daily away hours in your timezone.": "Apne timezone me roz ke away hours bhejo.",
    "Send your timezone offset from UTC.": "UTC se apne timezone ka difference bhejo.",
    "Use the format 22:00-07:00": "22:00-07:00 format me bhejo", "Use an offset like +5:30 or -4": "+5:30 ya -4 jaisa offset bhejo",
    "Away mode is on": "Away mode on", "Away mode is off.": "Away mode off.", "until": "tak",
    "This chat uses your default away message.": "Ye chat tumhara default away message use karegi.",
    "Away message for": "Away message iske liye",
    "Everything here works inside your own chats through your Business account.":
        "Yahan sab kuch tumhare Business account se tumhari apni chats me kaam karta hai.",
    "Keyword Replies": "Keyword Replies", "Keyword replies": "Keyword Replies", "Snippets": "Snippets",
    "Scheduled": "Scheduled", "Activity": "Activity", "Commands": "Commands", "Panic": "Panic",
    "Commands for your chats": "Tumhari chats ke commands",
    "Only you can use these, and the bot deletes the command message.": "Inhe sirf tum chala sakte ho, aur bot command wala message delete kar deta hai.",
    "Add Rule": "Rule Jodo", "Add Snippet": "Snippet Jodo", "No keyword rules yet.": "Abhi koi keyword rule nahi.",
    "No snippets yet.": "Abhi koi snippet nahi.",
    "When someone writes a keyword in a private chat, your account answers for you.":
        "Jab koi private chat me keyword likhta hai, tumhara account tumhari taraf se jawab deta hai.",
    "Type .name in any chat and your message turns into the saved text.":
        "Kisi bhi chat me .name likho aur tumhara message saved text me badal jayega.",
    "Send the keyword and the reply, separated by": "Keyword aur reply bhejo, beech me ye lagao",
    "Send the name and the text, separated by": "Name aur text bhejo, beech me ye lagao",
    "Use the format: keyword | reply": "Is format me bhejo: keyword | reply",
    "Use the format: name | text": "Is format me bhejo: name | text",
    "Keyword saved": "Keyword save ho gaya", "Removed": "Hata diya", "Not found": "Nahi mila",
    "Snippet saved. Use it with": "Snippet save ho gaya. Isse use karo",
    "Scheduled messages": "Scheduled Messages", "Nothing scheduled.": "Kuch schedule nahi hai.",
    "Recent activity": "Haal ki activity", "Nothing yet.": "Abhi kuch nahi.", "Scheduled for": "Schedule kiya gaya",
    "Panic done": "Panic ho gaya", "Repeaters Stopped": "Repeaters Band", "Shh Chats Off": "Shh Chats Off",
    "Scheduled Cleared": "Scheduled Hataye", "Away Mode": "Away Mode",
    "Saved message": "Saved message", "Reply to a message with .save": "Kisi message ko reply karke .save likho",
    "Could not save that message.": "Wo message save nahi ho saka.", "Translation": "Translation",
    "Translation failed, try again.": "Translation fail ho gaya, phir try karo.",
    "Shh is now": "Shh ab", "ON": "ON", "Shh ended": "Shh khatam", "Shh log": "Shh Log", "Ends": "Khatam",
    "Allowed Words": "Allowed Words", "Allowed Media": "Allowed Media", "Log": "Log", "none": "koi nahi", "never": "kabhi nahi",
    "Blocked words": "Blocked Words", "Link filter": "Link Filter", "Flood filter": "Flood Filter",
    "Choose your language.": "Apni language chuno.", "Done.": "Ho gaya.",
    "Media successfully archived": "Media successfully archive ho gaya",
    "Media retrieval failed. Please try again.": "Media nahi mil paya. Please phir try karo.",
    "No supported media found in the replied message.": "Reply kiye gaye message me koi supported media nahi mila.",
    "Your access has been disabled.": "Tumhara access band kar diya gaya hai.",
    "Contact Admin": "Admin Se Baat Karo", "Reply": "Reply Do", "You are the admin.": "Tum hi admin ho.",
    "Write your message for the admin. You can send text, a photo, voice note or file.":
        "Admin ke liye apna message likho. Tum text, photo, voice note ya file bhej sakte ho.",
    "Please wait a few seconds before sending another message.": "Dusra message bhejne se pehle kuch seconds ruko.",
    "Sent to the admin. The reply will arrive here.": "Admin ko bhej diya. Reply yahin aayega.",
    "Message from the admin": "Admin ka message",
    "That type is not supported. Send text, a photo, sticker, voice, video or file.":
        "Ye type supported nahi hai. Text, photo, sticker, voice, video ya file bhejo.",
    "Trigger Words": "Trigger Words", "Custom commands": "Custom Commands", "Custom command": "Custom Command",
    "Call history": "Call History", "Call": "Call", "Type": "Type", "Duration": "Duration",
    "Default": "Default", "Custom": "Custom", "Slots": "Slots", "Add Command": "Command Jodo", "Remove": "Hatao",
    "Send the word to archive with.": "Archive karne ke liye word bhejo.",
    "Letters, numbers or _ only.": "Sirf letters, numbers ya _ bhejo.",
    "Letters, numbers or _ only, max 24 characters.": "Sirf letters, numbers ya _, max 24 characters.",
    "That is already the default word, pick another.": "Ye pehle se default word hai, dusra chuno.",
    "Reply to any media with one of these words and the copy arrives here.":
        "Kisi bhi media ko inme se kisi word se reply karo aur copy yahin aa jayegi.",
    "The default word always works, custom ones are a premium feature.":
        "Default word hamesha chalta hai, custom words premium feature hai.",
    "Your 7 day trial has ended. Premium unlocks custom commands.":
        "Tumhara 7 din ka trial khatam ho gaya. Premium se custom commands chalu hoga.",
    "Your trial has ended. Premium unlocks custom commands.":
        "Tumhara trial khatam ho gaya. Premium se custom commands chalu hoga.",
    "New: custom archive commands": "Naya: Custom Archive Commands",
    "You can archive media with your own words now, up to": "Ab tum apne words se media archive kar sakte ho, total",
    "Reply to any media with your word, exactly like the default one.":
        "Kisi bhi media ko apne word se reply karo, bilkul default word ki tarah.",
    "7 day free trial unlocked": "7 din ka free trial chalu ho gaya",
    "Every call in your connected chats is recorded here automatically.":
        "Tumhari connected chats ki har call yahan apne aap record hoti hai.",
    "Call history is off for you.": "Tumhare liye call history band hai.",
})



# Every emoji the UI uses has a NAME and a normal-emoji fallback. If you give a name a
# custom (animated) emoji ID it is used everywhere that name appears, in text and buttons.
EMOJI = {
    "title": "✨", "star": "⭐", "camera": "📸", "bolt": "⚡", "user": "👤", "id": "🆔",
    "bot": "👾", "dev": "👨‍💻", "ok": "✅", "err": "❌", "warn": "⚠️", "time": "⏱",
    "pay": "💳", "box": "📦", "money": "💰", "calendar": "📅", "bell": "🔔", "ban": "🚫",
    "admin": "🛠", "mail": "✉️", "link": "🔗", "channel": "📢", "group": "👥",
    "stats": "📊", "lock": "🔒", "ghost": "👻", "home": "🏠", "bank": "🏦",
    "party": "🎉", "pin": "📝", "spin": "🌀", "globe": "🌐", "trash": "🗑", "gift": "🎁",
    "save": "💾", "list": "📋", "tools": "🧰", "play": "▶️", "chat": "💬",
}
# Built-in premium (animated) emoji IDs. Anything saved in emoji_ids.json (via /emojis)
# overrides these. Names without an ID here simply keep their normal emoji.
DEFAULT_EMOJI_IDS = {
    "title": "5424972470023104089",    # fire
    "star": "5438496463044752972",
    "bolt": "5224607267797606837",
    "ok": "5206607081334906820",
    "err": "5210952531676504517",
    "warn": "5447644880824181073",
    "time": "5440621591387980068",
    "pay": "5444856076954520455",      # receipt
    "box": "6046233639644042899",
    "money": "5278467510604160626",
    "calendar": "5413879192267805083",
    "bell": "5458603043203327669",
    "ban": "5260293700088511294",
    "admin": "5341715473882955310",
    "mail": "5253742260054409879",
    "link": "5271604874419647061",
    "channel": "5424818078833715060",
    "group": "5332724926216428039",
    "stats": "5231200819986047254",
    "lock": "5296369303661067030",
    "ghost": "5431609822288033666",
    "bank": "5332455502917949981",
    "party": "5276032951342088188",
    "pin": "5397782960512444700",
    "spin": "5375338737028841420",
}
EMOJI_IDS = {}


def load_emoji_ids():
    EMOJI_IDS.clear()
    if not USE_CUSTOM_EMOJI:
        return
    EMOJI_IDS.update(DEFAULT_EMOJI_IDS)
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
        [btn("Repeated Message", "bolt", callback_data="axmenu"),
         btn("Deleted Messages", "trash", callback_data="delmenu")],
        [btn("Auto Reply", "chat", callback_data="afkmenu"),
         btn("Tools", "tools", callback_data="toolsmenu")],
        [btn("Language", "globe", callback_data="langmenu"),
         btn("Contact Admin", "mail", callback_data="contact")],
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
         btn("Revenue", "money", callback_data="arevenue")],
        [btn("All Users", "user", callback_data="ausers:0"),
         btn("Premium Users", "star", callback_data="apremium")],
        [btn("User Info", "id", callback_data="aact:info"),
         btn("Broadcast", "channel", callback_data="abroadcast")],
        [btn("Give Premium", "gift", style="success", callback_data="aact:prem"),
         btn("Remove Premium", "err", callback_data="aact:unprem")],
        [btn("Ban User", "ban", style="danger", callback_data="aact:ban"),
         btn("Unban User", "ok", callback_data="aact:unban")],
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
# triggers              custom archive commands per user (premium)
# call_log              recorded calls from the connected chats
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
        CREATE TABLE IF NOT EXISTS ax_config (
            user_id INTEGER PRIMARY KEY,
            messages TEXT NOT NULL DEFAULT '[]',
            interval REAL NOT NULL DEFAULT 5,
            count INTEGER NOT NULL DEFAULT 10
        );
        CREATE TABLE IF NOT EXISTS shh_chats (
            connection_id TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            PRIMARY KEY(connection_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS ax_presets (
            user_id INTEGER NOT NULL, name TEXT NOT NULL, messages TEXT NOT NULL,
            interval REAL NOT NULL, interval_max REAL, count INTEGER NOT NULL,
            PRIMARY KEY(user_id, name)
        );
        CREATE TABLE IF NOT EXISTS chat_rules (
            connection_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
            block_words TEXT NOT NULL DEFAULT '[]', no_links INTEGER NOT NULL DEFAULT 0,
            antispam INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(connection_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS afk_config (
            user_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, message TEXT,
            until REAL, sched_on INTEGER NOT NULL DEFAULT 0, start_min INTEGER, end_min INTEGER
        );
        CREATE TABLE IF NOT EXISTS afk_chat (
            connection_id TEXT NOT NULL, chat_id INTEGER NOT NULL, message TEXT NOT NULL,
            PRIMARY KEY(connection_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS kw_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            keyword TEXT NOT NULL, reply TEXT NOT NULL, UNIQUE(user_id, keyword)
        );
        CREATE TABLE IF NOT EXISTS snippets (
            user_id INTEGER NOT NULL, name TEXT NOT NULL, text TEXT NOT NULL,
            PRIMARY KEY(user_id, name)
        );
        CREATE TABLE IF NOT EXISTS scheduled (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, owner_id INTEGER NOT NULL,
            connection_id TEXT NOT NULL, chat_id INTEGER NOT NULL, label TEXT,
            text TEXT, message_id INTEGER, run_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sched_run ON scheduled(run_at);
        CREATE TABLE IF NOT EXISTS msg_cache (
            connection_id TEXT NOT NULL, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
            sender_id INTEGER, sender_name TEXT, sender_username TEXT,
            kind TEXT NOT NULL, text TEXT, file_id TEXT, sent_at TEXT NOT NULL,
            PRIMARY KEY(connection_id, chat_id, message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_cache_time ON msg_cache(sent_at);
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, ts REAL NOT NULL,
            kind TEXT NOT NULL, chat_id INTEGER, label TEXT, detail TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_activity_user ON activity(user_id, ts);
        CREATE INDEX IF NOT EXISTS idx_conn_owner ON business_connections(owner_id);
        CREATE TABLE IF NOT EXISTS triggers (
            user_id INTEGER NOT NULL,
            word TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(user_id, word)
        );
        CREATE TABLE IF NOT EXISTS call_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            chat_label TEXT,
            direction TEXT,
            duration INTEGER,
            ts REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_calls_user ON call_log(user_id, ts);
        """)

        # Migration: per-user limit column for databases created by older versions.
        cols = [r["name"] for r in con.execute("PRAGMA table_info(users)")]
        if "custom_limit" not in cols:
            con.execute("ALTER TABLE users ADD COLUMN custom_limit INTEGER")

        # Migrations for the newer features (safe to run on every start).
        def add_col(table, name, ddl):
            existing = [r["name"] for r in con.execute(f"PRAGMA table_info({table})")]
            if name in existing:
                return False
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
            return True

        add_col("ax_config", "interval_max", "REAL")
        for col, ddl in (("until", "REAL"), ("allow_words", "TEXT"), ("allow_media", "TEXT"),
                         ("log", "INTEGER NOT NULL DEFAULT 0"), ("label", "TEXT")):
            add_col("shh_chats", col, ddl)
        add_col("users", "lang", "TEXT NOT NULL DEFAULT 'en'")
        add_col("users", "save_deleted", "INTEGER NOT NULL DEFAULT 1")
        add_col("users", "tz_offset", "INTEGER NOT NULL DEFAULT 0")
        add_col("users", "trial_notified", "INTEGER NOT NULL DEFAULT 0")
        if add_col("users", "trial_until", "TEXT"):
            # everybody who already exists gets the free trial, starting now
            con.execute("UPDATE users SET trial_until=?", ((now() + timedelta(days=TRIAL_DAYS)).isoformat(),))
        add_col("users", "cmd_notified", "INTEGER NOT NULL DEFAULT 0")
        if add_col("users", "cmd_trial_until", "TEXT"):
            # every existing user gets the 7 day custom-command trial, starting now
            con.execute("UPDATE users SET cmd_trial_until=?",
                        ((now() + timedelta(days=TRIGGER_TRIAL_DAYS)).isoformat(),))

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
    """Create or refresh a user. A brand-new user starts with the free trials."""
    stamp = now().isoformat()
    trial_end = (now() + timedelta(days=TRIAL_DAYS)).isoformat()
    cmd_trial_end = (now() + timedelta(days=TRIGGER_TRIAL_DAYS)).isoformat()
    with db() as con:
        con.execute("""
            INSERT INTO users(user_id, username, first_name, joined_at, last_seen, trial_until, cmd_trial_until)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
              username=excluded.username,
              first_name=excluded.first_name,
              last_seen=excluded.last_seen
        """, (user.id, user.username, user.first_name, stamp, stamp, trial_end, cmd_trial_end))


def get_user(user_id):
    with db() as con:
        return con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def is_banned(user_id):
    row = get_user(user_id)
    return bool(row and row["banned"])


def find_user(token):
    """'@username' or a numeric ID -> the users row (only people who started the bot)."""
    token = (token or "").strip()
    if not token:
        return None
    if token.lstrip("-").isdigit():
        return get_user(int(token))
    return find_registered_user(token)


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


# ---- trial, language, links, durations (shared helpers) -----------------------------
def trial_expiry(user_id):
    """End of the free trial, or None when there is none / it is over."""
    row = get_user(user_id)
    if not row or not row["trial_until"]:
        return None
    try:
        end = datetime.fromisoformat(row["trial_until"])
    except ValueError:
        return None
    return end if end > now() else None


def has_pro(user_id):
    """Premium features (deleted-message saver): paid/granted premium OR the free trial."""
    return is_premium(user_id) or trial_expiry(user_id) is not None


def deleted_saver_active(user_id):
    row = get_user(user_id)
    return bool(row and row["save_deleted"] and has_pro(user_id))


def cmd_trial_expiry(user_id):
    """End of the custom-command trial, or None when there is none / it is over."""
    row = get_user(user_id)
    if not row or not row["cmd_trial_until"]:
        return None
    try:
        end = datetime.fromisoformat(row["cmd_trial_until"])
    except ValueError:
        return None
    return end if end > now() else None


def has_custom_triggers(user_id):
    """Custom commands: paid premium OR the 7 day trial."""
    return is_premium(user_id) or cmd_trial_expiry(user_id) is not None


def user_triggers(user_id):
    return _TRIGGERS.get(user_id, set())


def trigger_words(user_id):
    """Every word that archives for this user. The default one always works."""
    words = {TRIGGER_WORD}
    if has_custom_triggers(user_id):
        words |= user_triggers(user_id)
    return words


def add_trigger(user_id, word):
    word = word.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{1,24}", word):
        return "format"
    if word == TRIGGER_WORD:
        return "default"
    with db() as con:
        exists = con.execute("SELECT 1 FROM triggers WHERE user_id=? AND word=?", (user_id, word)).fetchone()
        n = con.execute("SELECT COUNT(*) n FROM triggers WHERE user_id=?", (user_id,)).fetchone()["n"]
        if not exists and n >= MAX_TRIGGERS:
            return "limit"
        con.execute("INSERT INTO triggers(user_id, word, created_at) VALUES(?,?,?) "
                    "ON CONFLICT(user_id, word) DO NOTHING", (user_id, word, now().isoformat()))
    _TRIGGERS.setdefault(user_id, set()).add(word)
    return "ok"


def remove_trigger(user_id, word):
    word = word.lower()
    with db() as con:
        n = con.execute("DELETE FROM triggers WHERE user_id=? AND word=?", (user_id, word)).rowcount
    _TRIGGERS.get(user_id, set()).discard(word)
    return n


def fmt_left(delta):
    secs = max(0, int(delta.total_seconds()))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{max(m, 1)}m"


def pro_status_text(user_id):
    if is_premium(user_id):
        return sc("Active")
    end = trial_expiry(user_id)
    if end:
        return f"{sc('Free Trial')} ({fmt_left(end - now())})"
    return sc("Locked")


_LANG_CACHE = {}


def get_lang(user_id):
    if user_id is None:
        return "en"
    lang = _LANG_CACHE.get(user_id)
    if lang is None:
        row = get_user(user_id)
        lang = (row["lang"] if row and row["lang"] else "en")
        _LANG_CACHE[user_id] = lang
    return lang


def set_lang(user_id, lang):
    with db() as con:
        con.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, user_id))
    _LANG_CACHE[user_id] = lang


@contextmanager
def use_lang(user_id):
    """Write texts in THIS user's language (used when messaging someone else)."""
    token = _LANG.set(get_lang(user_id))
    try:
        yield
    finally:
        _LANG.reset(token)


def user_tz(user_id):
    row = get_user(user_id)
    return int(row["tz_offset"]) if row and row["tz_offset"] is not None else 0


def fmt_tz(minutes):
    sign = "+" if minutes >= 0 else "-"
    h, m = divmod(abs(minutes), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def parse_tz(text):
    m = re.fullmatch(r"(?:utc)?\s*([+-])?\s*(\d{1,2})(?::?(\d{2}))?", text.strip().lower())
    if not m:
        return None
    minutes = int(m.group(2)) * 60 + int(m.group(3) or 0)
    if m.group(1) == "-":
        minutes = -minutes
    return minutes if -12 * 60 <= minutes <= 14 * 60 else None


def fmt_local(ts, user_id, with_date=True):
    local = datetime.fromtimestamp(ts, timezone.utc) + timedelta(minutes=user_tz(user_id))
    return local.strftime("%d %b %H:%M" if with_date else "%H:%M")


_DUR_RE = re.compile(r"^(\d+(?:\.\d+)?)([smhd])$", re.I)


def parse_duration(text):
    m = _DUR_RE.match((text or "").strip())
    if not m:
        return None
    return float(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2).lower()]


def parse_clock(text):
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", (text or "").strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def parse_when(token, user_id):
    """'30s' '10m' '2h' '1d'  or a clock time '21:30' (next time it happens, in YOUR timezone)."""
    dur = parse_duration(token)
    if dur is not None:
        return time.time() + dur
    minutes = parse_clock(token)
    if minutes is None:
        return None
    tz = user_tz(user_id)
    local = now() + timedelta(minutes=tz)
    target = local.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return (target - timedelta(minutes=tz)).timestamp()


def name_link(user_id, username=None, first_name=None, last_name=None):
    """@username when there is one, otherwise the profile name as a tap-to-open link."""
    if username:
        return f"@{esc(username)}"
    full = " ".join(p for p in (first_name, last_name) if p) or str(user_id)
    return f'<a href="tg://user?id={user_id}">{esc(full)}</a>'


def chat_link(chat):
    if chat.type == "private":
        return name_link(chat.id, chat.username, chat.first_name, chat.last_name)
    title = chat.title or str(chat.id)
    if chat.username:
        return f'<a href="https://t.me/{esc(chat.username)}">{esc(title)}</a>'
    return esc(title)


def plain_label(chat):
    """Short plain-text name of a chat (stored in logs): @username or the profile name."""
    if chat.username:
        return f"@{chat.username}"
    if chat.type == "private":
        return " ".join(p for p in (chat.first_name, chat.last_name) if p) or str(chat.id)
    return chat.title or str(chat.id)


def chat_ref(chat_id, label=None):
    """HTML for a stored chat label: @username, or the name as a link to the profile."""
    if label and label.startswith("@"):
        return esc(label)
    shown = esc(label or chat_id)
    return f'<a href="tg://user?id={chat_id}">{shown}</a>' if chat_id and chat_id > 0 else shown


def db_user_link(user_id):
    row = get_user(user_id)
    if row:
        return name_link(user_id, row["username"], row["first_name"])
    return name_link(user_id)


def log_activity(user_id, kind, chat_id=None, label=None, detail=""):
    try:
        with db() as con:
            con.execute("INSERT INTO activity(user_id, ts, kind, chat_id, label, detail) VALUES(?,?,?,?,?,?)",
                        (user_id, time.time(), kind, chat_id, label, str(detail)[:200]))
    except Exception:
        logging.exception("activity log failed")


# ---- message contents (used by the deleted-message saver, .save and the shh log) ------
MEDIA_KINDS = ("photo", "video", "voice", "audio", "document", "animation", "video_note", "sticker")


def msg_kind(m):
    if m.text:
        return "text"
    for kind in MEDIA_KINDS:
        if getattr(m, kind, None):
            return kind
    return "other"


def msg_payload(m):
    kind = msg_kind(m)
    if kind == "text":
        return {"kind": "text", "text": m.text, "file_id": None}
    if kind == "photo":
        return {"kind": "photo", "text": m.caption or "", "file_id": m.photo[-1].file_id}
    if kind in MEDIA_KINDS:
        return {"kind": kind, "text": m.caption or "", "file_id": getattr(m, kind).file_id}
    return {"kind": "other", "text": "[unsupported message type]", "file_id": None}


_CAPTION_SENDERS = {
    "photo": ("send_photo", "photo"), "video": ("send_video", "video"),
    "voice": ("send_voice", "voice"), "audio": ("send_audio", "audio"),
    "document": ("send_document", "document"), "animation": ("send_animation", "animation"),
}


async def send_payload(chat_id, p, header, reply_markup=None):
    """Send a stored message (text or file_id) to `chat_id` with an HTML header above it."""
    kind, text = p["kind"], p.get("text") or ""
    if kind in ("text", "other") or not p.get("file_id"):
        return await bot.send_message(chat_id, header + ("\n\n" + esc(text[:3500]) if text else ""),
                                      reply_markup=reply_markup)
    if kind in _CAPTION_SENDERS:
        meth, arg = _CAPTION_SENDERS[kind]
        cap = header + ("\n\n" + esc(text[:600]) if text else "")
        return await getattr(bot, meth)(chat_id, **{arg: p["file_id"]}, caption=cap, reply_markup=reply_markup)
    # stickers and video notes cannot carry a caption: send the file, then the header under it
    sent = await (bot.send_sticker(chat_id, p["file_id"]) if kind == "sticker"
                  else bot.send_video_note(chat_id, p["file_id"]))
    await bot.send_message(chat_id, header, reply_to_message_id=sent.message_id, reply_markup=reply_markup)
    return sent


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
    if connection.is_enabled:
        _CONN_OWNER[connection.id] = connection.user.id
    else:
        _CONN_OWNER.pop(connection.id, None)
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
    words = len(user_triggers(user_id)) + 1
    return "\n".join([
        bq(f"{E('user')} {sc('Welcome')}, {esc(name or 'friend')}"),
        head("title", "Media Archive Bot", " ❗️"),
        "",
        row("star", "Plan", plan),
        row("camera", "Today", used),
        row("trash", "Deleted Saver", pro_status_text(user_id)),
        row("bolt", "Trigger Words", words),
        SEP,
        f"{E('bolt')} {sc('Reply to any media with')} <code>{TRIGGER_WORD}</code>",
    ])


def privacy_lines():
    """What we tell users about storage. It always matches what the bot really does."""
    if SAVE_MEDIA_LOCALLY:
        return [f"{E('box')} {sc('A backup copy is also kept in our private archive.')}"]
    if TG_MEDIA_BACKUP:
        return [f"{E('lock')} {sc('For privacy, nothing is stored on our server and only you can see your saved photos and media here in your bot.')}"]
    return [f"{E('lock')} {sc('For privacy reasons no backup is stored. Only you can see your photos and media here in your bot.')}"]


def howto_text():
    return "\n".join([
        bq(f"{E('camera')} {sc('How to archive media')}"),
        "",
        row("pin", "Step 1", sc("Start this bot")),
        row("pin", "Step 2", sc("Settings → Business → Chatbots → add this bot")),
        row("pin", "Step 3", f"{sc('In any chat, reply to a media with')} <code>{TRIGGER_WORD}</code>"),
        row("box", "Step 4", sc("The copy arrives here, in your private chat")),
        SEP,
        *privacy_lines(),
        SEP,
        f"{E('play')} <a href=\"{html.escape(TUTORIAL_URL, quote=True)}\">{sc('Watch the video tutorial')}</a>",
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
    try:
        with use_lang(user_id):
            text = "\n".join([
                bq(f"{E('party')} {sc('Congratulations!')}"),
                head("star", "Premium Activated", " ❗️"),
                "",
                row("calendar", "Valid Till", f"{expiry:%Y-%m-%d %H:%M} UTC"),
                row("bolt", "Daily Limit", sc("Unlimited")),
                row("trash", "Deleted Saver", sc("Active")),
            ])
            await bot.send_message(user_id, text, reply_markup=back_keyboard())
        return True
    except Exception:
        logging.exception("Could not notify %s about premium", user_id)
        return False


class AdminFlow(StatesGroup):
    broadcast_text = State()
    dm_target = State()
    dm_text = State()
    target = State()
    prem_days = State()
    reply = State()


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
    row_ = get_user(uid)
    if row_ and not row_["trial_notified"]:      # new users (and old ones who missed it) hear about the trial
        await send_trial_intro(uid)
    if row_ and not row_["cmd_notified"]:
        await send_trigger_intro(uid)


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
        row_ = get_user(uid)
        if row_ and not row_["trial_notified"]:
            await send_trial_intro(uid)
        if row_ and not row_["cmd_notified"]:
            await send_trigger_intro(uid)
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
    await safe_edit(callback, howto_text(), back_keyboard([btn("Watch Tutorial", "play", url=TUTORIAL_URL)]))
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
            row("trash", "Deleted Saver", pro_status_text(uid)),
            row("bolt", "Trigger Words", len(user_triggers(uid))),
        ])
        kb = back_keyboard([btn("Extend Premium", "pay", style="primary", callback_data="buy")])
    else:
        text = "\n".join([
            bq(f"{E('user')} {sc('You are on the free plan')}"),
            "",
            row("bolt", "Daily Limit", user_daily_limit(uid)),
            row("camera", "Used Today", daily_count(uid)),
            row("trash", "Deleted Saver", pro_status_text(uid)),
            row("bolt", "Trigger Words", len(user_triggers(uid))),
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
        calls = con.execute("SELECT COUNT(*) n FROM call_log WHERE user_id=?", (uid,)).fetchone()["n"]
    limit = user_daily_limit(uid)
    text = "\n".join([
        bq(f"{E('stats')} {sc('Your statistics')}"),
        "",
        row("box", "Total Archived", total),
        row("camera", "Today", f"{daily_count(uid)} / {'∞' if limit is None else limit}"),
        row("chat", "Call history", calls),
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


def money(n):
    return f"₹{int(n):,}"


def _revenue(con, since=None):
    """Money that users PAID through the bot (approved payments). Premium you give with
    /premium or the Give Premium button is never counted here."""
    sql = "SELECT COALESCE(SUM(amount),0) s, COUNT(*) n FROM payments WHERE status='approved'"
    args = ()
    if since is not None:
        sql += " AND reviewed_at>=?"
        args = (since.isoformat(),)
    r = con.execute(sql, args).fetchone()
    return r["s"], r["n"]


@dp.callback_query(F.data == "astats")
async def admin_stats(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    d0 = now().replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = d0.isoformat()
    stamp = now().isoformat()
    with db() as con:
        total = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        active = con.execute("SELECT COUNT(*) n FROM users WHERE last_seen>=?",
                             ((now() - timedelta(days=7)).isoformat(),)).fetchone()["n"]
        premium = con.execute("SELECT COUNT(*) n FROM premium WHERE expires_at>?", (stamp,)).fetchone()["n"]
        trial = con.execute(
            "SELECT COUNT(*) n FROM users WHERE trial_until>? AND user_id NOT IN "
            "(SELECT user_id FROM premium WHERE expires_at>?)", (stamp, stamp)).fetchone()["n"]
        banned = con.execute("SELECT COUNT(*) n FROM users WHERE banned=1").fetchone()["n"]
        joined = con.execute("SELECT COUNT(*) n FROM users WHERE joined_at>=?", (day_start,)).fetchone()["n"]
        archived = con.execute("SELECT COALESCE(SUM(count),0) n FROM usage_daily WHERE day=?",
                               (today(),)).fetchone()["n"]
        calls = con.execute("SELECT COUNT(*) n FROM call_log WHERE ts>=?",
                            (d0.timestamp(),)).fetchone()["n"]
        rev_total, rev_n = _revenue(con)
        rev_today, _ = _revenue(con, d0)
        rev_month, _ = _revenue(con, d0.replace(day=1))
    text = "\n".join([
        bq(f"{E('stats')} {sc('Admin statistics')}"),
        "",
        row("group", "Total Users", total),
        row("bolt", "Active 7 Days", active),
        row("star", "Premium Users", premium),
        row("gift", "On Free Trial", trial),
        row("ban", "Banned Users", banned),
        row("title", "Joined Today", joined),
        row("camera", "Archives Today", archived),
        row("chat", "Calls Today", calls),
        row("pay", "Pending Payments", pending_payments()),
        SEP,
        row("money", "Revenue Total", f"{money(rev_total)} ({rev_n} {sc('sales')})"),
        row("money", "Revenue Today", money(rev_today)),
        row("money", "Revenue Month", money(rev_month)),
    ])
    await safe_edit(callback, text, admin_keyboard())
    await callback.answer()


@dp.callback_query(F.data == "arevenue")
async def admin_revenue(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    d0 = now().replace(hour=0, minute=0, second=0, microsecond=0)
    with db() as con:
        total = _revenue(con)
        today_ = _revenue(con, d0)
        week = _revenue(con, d0 - timedelta(days=6))
        days30 = _revenue(con, d0 - timedelta(days=29))
        month = _revenue(con, d0.replace(day=1))
        months = con.execute(
            "SELECT substr(reviewed_at,1,7) m, SUM(amount) s, COUNT(*) n FROM payments "
            "WHERE status='approved' GROUP BY m ORDER BY m DESC LIMIT 6").fetchall()
        plans = con.execute(
            "SELECT months, COUNT(*) n, SUM(amount) s FROM payments WHERE status='approved' "
            "GROUP BY months ORDER BY months").fetchall()

    def line(label, pair):
        return row("money", label, f"{money(pair[0])} ({pair[1]})")

    lines = [bq(f"{E('money')} {sc('Premium revenue')}"), "",
             line("All Time", total), line("Today", today_), line("Last 7 Days", week),
             line("Last 30 Days", days30), line("This Month", month)]
    if months:
        lines += [SEP, f"{E('calendar')} <b>{sc('By month')}</b>"]
        lines += [f"{r['m']} ➛ {money(r['s'])} ({r['n']})" for r in months]
    if plans:
        lines += [SEP, f"{E('star')} <b>{sc('By plan')}</b>"]
        lines += [f"{sc(month_label(r['months']))} ➛ {r['n']} × ➛ {money(r['s'])}" for r in plans]
    lines += [SEP, f"{E('lock')} {sc('Only plans users bought and you approved are counted. Premium you gave yourself is not.')}"]
    await safe_edit(callback, "\n".join(lines), admin_keyboard())
    await callback.answer()


# ---- all users list (pages) ----------------------------------------------------------
USERS_PER_PAGE = 15


def users_page(page):
    with db() as con:
        total = con.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        pages = max(1, -(-total // USERS_PER_PAGE))
        page = min(max(page, 0), pages - 1)
        rows = con.execute(
            "SELECT user_id, username, first_name, banned FROM users "
            "ORDER BY joined_at DESC LIMIT ? OFFSET ?", (USERS_PER_PAGE, page * USERS_PER_PAGE)).fetchall()
        prem = {r["user_id"] for r in con.execute(
            "SELECT user_id FROM premium WHERE expires_at>?", (now().isoformat(),))}
    lines = [bq(f"{E('group')} {sc('All users')} ({total})"),
             f"{sc('Page')} {page + 1}/{pages}", ""]
    for i, r in enumerate(rows, page * USERS_PER_PAGE + 1):
        flags = (" ⭐" if r["user_id"] in prem else "") + (" 🚫" if r["banned"] else "")
        lines.append(f"{i}. {name_link(r['user_id'], r['username'], r['first_name'])} "
                     f"➛ <code>{r['user_id']}</code>{flags}")
    nav = []
    if page > 0:
        nav.append(btn("◀ Prev", callback_data=f"ausers:{page - 1}"))
    nav.append(btn(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(btn("Next ▶", callback_data=f"ausers:{page + 1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [btn("Download List", "box", callback_data="ausersfile")],
        [btn("Admin Panel", "admin", callback_data="admin")],
    ])
    return "\n".join(lines), kb


@dp.callback_query(F.data.startswith("ausers:"))
async def admin_users(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    text, kb = users_page(int(callback.data.split(":")[1]))
    await safe_edit(callback, text, kb)
    await callback.answer()


@dp.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data == "ausersfile")
async def admin_users_file(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    with db() as con:
        rows = con.execute(
            "SELECT u.user_id, u.username, u.first_name, u.joined_at, u.last_seen, u.banned, "
            "p.expires_at FROM users u LEFT JOIN premium p ON p.user_id=u.user_id "
            "ORDER BY u.joined_at").fetchall()
    buf = StringIO()
    writer = csv.writer(buf)
    writer.writerow(["user_id", "username", "first_name", "joined_at", "last_seen", "banned", "premium_until"])
    for r in rows:
        writer.writerow([r["user_id"], r["username"] or "", r["first_name"] or "", r["joined_at"],
                         r["last_seen"], r["banned"], r["expires_at"] or ""])
    doc = BufferedInputFile(buf.getvalue().encode("utf-8-sig"), filename=f"users_{today()}.csv")
    remember(await callback.message.answer_document(doc, caption=f"{len(rows)} users"))
    await callback.answer()


@dp.callback_query(F.data == "apremium")
async def admin_premium_list(callback: CallbackQuery):
    if not await admin_only(callback):
        return
    with db() as con:
        rows = con.execute("""
            SELECT u.username, u.first_name, u.user_id, p.expires_at
            FROM premium p JOIN users u ON u.user_id=p.user_id
            WHERE p.expires_at>? ORDER BY p.expires_at
        """, (now().isoformat(),)).fetchall()
    lines = [bq(f"{E('star')} {sc('Active premium users')}"), ""]
    if not rows:
        lines.append(sc("None yet."))
    for r in rows[:40]:
        lines.append(f"{E('star')} {name_link(r['user_id'], r['username'], r['first_name'])} ➛ {r['expires_at'][:10]}")
    await safe_edit(callback, "\n".join(lines), admin_keyboard())
    await callback.answer()


# ---- admin actions by button: info / ban / unban / give / remove premium --------------
ADMIN_PROMPTS = {
    "info": "Send the @username or numeric ID of the user to look up.",
    "ban": "Send the @username or numeric ID of the user to ban.",
    "unban": "Send the @username or numeric ID of the user to unban.",
    "prem": "Send the @username or numeric ID of the user to give premium to.",
    "unprem": "Send the @username or numeric ID of the user to remove premium from.",
}


def user_info_text(user):
    uid = user["user_id"]
    expiry = premium_expiry(uid)
    trial = trial_expiry(uid)
    cmd_trial = cmd_trial_expiry(uid)
    limit = user_daily_limit(uid)
    with db() as con:
        total = con.execute("SELECT COUNT(*) n FROM archives WHERE user_id=?", (uid,)).fetchone()["n"]
        conns = con.execute("SELECT COUNT(*) n FROM business_connections WHERE owner_id=?", (uid,)).fetchone()["n"]
    return "\n".join([
        bq(f"{E('user')} {name_link(uid, user['username'], user['first_name'])}"),
        "",
        row("id", "ID", f"<code>{uid}</code>"),
        row("ban", "Banned", sc(str(bool(user["banned"])))),
        row("star", "Premium", f"{expiry:%Y-%m-%d %H:%M} UTC" if expiry else sc("No")),
        row("gift", "Free Trial", f"{trial:%Y-%m-%d %H:%M} UTC" if trial else sc("No")),
        row("bolt", "Trigger Trial", f"{cmd_trial:%Y-%m-%d %H:%M} UTC" if cmd_trial else sc("No")),
        row("bolt", "Trigger Words", ", ".join(sorted(user_triggers(uid))) or sc("none")),
        row("trash", "Deleted Saver", sc("On") if user["save_deleted"] else sc("Off")),
        row("globe", "Language", esc(user["lang"])),
        row("bolt", "Daily Limit", "∞" if limit is None else limit),
        row("camera", "Used Today", daily_count(uid)),
        row("box", "Total Archived", total),
        row("link", "Connections", conns),
        row("calendar", "Joined", user["joined_at"][:16].replace("T", " ")),
        row("time", "Last Seen", user["last_seen"][:16].replace("T", " ")),
    ])


@dp.callback_query(F.data.startswith("aact:"))
async def admin_action_prompt(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    act = callback.data.split(":")[1]
    if act not in ADMIN_PROMPTS:
        return await callback.answer()
    await state.set_state(AdminFlow.target)
    await state.update_data(act=act)
    remember(await callback.message.answer(
        f"{E('id')} {sc(ADMIN_PROMPTS[act])}\n{sc('Send /cancel to stop.')}"))
    await callback.answer()


async def apply_admin_action(chat, act, user):
    uid = user["user_id"]
    who = name_link(uid, user["username"], user["first_name"])
    if act == "info":
        return await notice(chat, user_info_text(user), ttl=90)
    if act == "ban":
        if uid == OWNER_ID:
            return await notice(chat, f"{E('warn')} {sc('You cannot ban yourself.')}")
        with db() as con:
            con.execute("UPDATE users SET banned=1 WHERE user_id=?", (uid,))
        return await notice(chat, f"{E('ban')} {sc('User banned')} ➛ {who}", ttl=8)
    if act == "unban":
        with db() as con:
            con.execute("UPDATE users SET banned=0 WHERE user_id=?", (uid,))
        return await notice(chat, f"{E('ok')} {sc('User unbanned')} ➛ {who}", ttl=8)
    if act == "unprem":
        with db() as con:
            con.execute("DELETE FROM premium WHERE user_id=?", (uid,))
        return await notice(chat, f"{E('ok')} {sc('Premium removed for')} {who}", ttl=8)


@dp.message(AdminFlow.target)
async def admin_action_target(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    if message.text == "/cancel":
        await state.clear()
        await sweep(chat)
        return await notice(chat, f"{E('err')} {sc('Cancelled.')}", ttl=5)
    user = find_user(message.text or "")
    if not user:
        return await notice(chat, f"{E('warn')} {sc('User not found. They must have started the bot. Try again or /cancel.')}", ttl=8)
    act = (await state.get_data()).get("act")
    await sweep(chat)
    if act == "prem":
        await state.update_data(uid=user["user_id"])
        await state.set_state(AdminFlow.prem_days)
        who = name_link(user["user_id"], user["username"], user["first_name"])
        remember(await message.answer(f"{E('calendar')} {sc('How many days of premium for')} {who}?"))
        return
    await state.clear()
    await apply_admin_action(chat, act, user)


@dp.message(AdminFlow.prem_days)
async def admin_prem_days(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    if message.text == "/cancel":
        await state.clear()
        await sweep(chat)
        return await notice(chat, f"{E('err')} {sc('Cancelled.')}", ttl=5)
    text = (message.text or "").strip()
    if not text.isdigit() or int(text) < 1:
        return await notice(chat, f"{E('warn')} {sc('Send a number of days, like 30.')}", ttl=6)
    uid = (await state.get_data())["uid"]
    await state.clear()
    await sweep(chat)
    expiry = grant_premium(uid, int(text), extend=True)
    notified = await notify_premium(uid, expiry)
    await notice(chat,
                 f"{E('star')} {sc('Premium until')} {expiry.date()} ➛ {db_user_link(uid)}\n"
                 + (f"{E('bell')} {sc('User has been notified.')}" if notified
                    else f"{E('warn')} {sc('Could not notify the user.')}"), ttl=12)


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
    for months, (days, price) in PLANS.items():
        lines.append(row("star", month_label(months), f"₹{price} ➛ {days} {sc('days')}"))
    lines += [SEP, f"{E('bolt')} {sc('Unlimited archives on every plan.')}",
              f"{E('pay')} {sc('Select a plan to get your payment QR.')}"]
    return "\n".join(lines)


def plans_keyboard():
    rows = [[btn(f"{month_label(m)} · {days}d — ₹{price}", "star", callback_data=f"plan:{m}")]
            for m, (days, price) in PLANS.items()]
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
        _TASKS.add(asyncio.create_task(backup_now("payment")))  # never lose a paid premium
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
        return await notice(chat, "Usage: <code>/premium @username days</code> (" + sc("an ID works too") + ")\nExample: <code>/premium @example 30</code>", ttl=10)
    user = find_user(parts[1])
    if not user:
        return await notice(chat, sc("User not found. They must have started this bot first."), ttl=10)
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
    user = find_user(parts[1])
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
    user = find_user(parts[1])
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
    """/userinfo @username   or   /userinfo 123456789"""
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    parts = (message.text or "").split()
    if len(parts) != 2:
        return await notice(chat, "Usage: <code>/userinfo @username</code> " + sc("or") + " <code>/userinfo 123456789</code>", ttl=10)
    user = find_user(parts[1])
    if not user:
        return await notice(chat, sc("User not found. They must have started the bot."))
    await notice(chat, user_info_text(user), ttl=90)  # long enough to read, then it cleans itself


@dp.message(Command("ban"))
async def ban_user(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    await tidy(message)
    parts = (message.text or "").split()
    if len(parts) != 2:
        return await notice(chat, "Usage: <code>/ban @username</code>", ttl=10)
    user = find_user(parts[1])
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
    user = find_user(parts[1])
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
        return await bot.send_photo(photo=upload, **cap, **common)
    if media_type == "video":
        return await bot.send_video(video=upload, **cap, **common)
    if media_type == "animation":
        return await bot.send_animation(animation=upload, **cap, **common)
    if media_type == "document":
        return await bot.send_document(document=upload, **cap, **common)
    if media_type == "video_note":
        return await bot.send_video_note(video_note=upload, **common)  # video notes have no caption


# Private channel that keeps a permanent copy of every archived file (survives host crashes).
# Needs the NUMERIC id (-100...), not the invite link. Add the bot as admin with "post messages".
# If MEDIA_CHAT_ID is not set it falls back to BACKUP_CHAT_ID, and then to your own chat with the bot.
MEDIA_CHAT_ID = int(os.getenv("MEDIA_CHAT_ID", "0")) or int(os.getenv("BACKUP_CHAT_ID", "0")) or OWNER_ID


async def store_media_copy(sent: Message, owner_id, chat, media_type):
    """Copy the file the owner just received into the media channel (no re-upload).
    The caption names owner and chat by @username, or by profile name as a tap-to-open link."""
    if not TG_MEDIA_BACKUP or not MEDIA_CHAT_ID or sent is None:
        return
    try:
        info = "\n".join([
            row("user", "Owner", f"{db_user_link(owner_id)} · <code>{owner_id}</code>"),
            row("chat", "Chat", f"{chat_link(chat)} · <code>{chat.id}</code>"),
            row("box", media_type, f"{now():%Y-%m-%d %H:%M} UTC"),
        ])
        copied = await bot.copy_message(
            MEDIA_CHAT_ID, from_chat_id=sent.chat.id, message_id=sent.message_id,
            caption=info, parse_mode="HTML")
        if media_type == "video_note":  # video notes cannot carry a caption
            await bot.send_message(MEDIA_CHAT_ID, info, reply_to_message_id=copied.message_id)
    except Exception:
        logging.exception("Could not copy media to the media channel")


# ============================================================================
# BUSINESS TOOLS -- they work inside YOUR chats through the connected Business account.
# Type these in any chat (the bot removes the command message again):
#
#   .Axclerate [preset] [count interval] [msg | msg]   repeat messages (see /start -> Repeated Message)
#   .Axpause  .Axresume  .Axstop                       control the repeater in this chat
#   .shh [30m]            toggle: delete what the OTHER person sends (this chat only)
#   .shh allow w1 | w2    keep messages containing these words     .shh allowmedia photo voice
#   .shh allow clear      .shh log (save a copy of what is deleted)  .shh off
#   .afk [2h] [message]   away auto-reply on      .back  off        .afkhere message|off
#   .later 30m text       send later (or .later 21:30 text)
#   .save                 reply to a message to save it into the bot
#   .del 10s text         send text that deletes itself   (or reply to your own message)
#   .tr hi text           translate your text, or reply with .tr en to read a message
#   .block w1 | w2        delete messages with these words    .block off
#   .nolinks   .antispam  toggle link filter / flood filter for this chat
#   .kw add word | reply  .kw del word  .kw list       keyword auto-replies
#   .snip add name | text .snip del name  .snip list   snippets, use them with  .name
#   .panic                stop repeaters, shh, scheduled sends and away mode
# ============================================================================
AX_MIN_INTERVAL = 0.1         # seconds (the only limit; Telegram itself may slow you down)
AX_WARNING = "Spamming can get your Telegram account limited or banned. Use at your own risk."
AFK_COOLDOWN = 6 * 3600       # one away-reply per chat every 6 hours
AFK_DEFAULT_TEXT = "I'm away right now. I'll get back to you later."
KW_COOLDOWN = 60              # seconds, per chat and keyword
ANTISPAM_LIMIT, ANTISPAM_WINDOW = 5, 10   # more than 5 messages in 10 s from one person
MAX_KEYWORDS, MAX_SNIPPETS, MAX_PRESETS, MAX_SCHEDULED = 20, 30, 10, 50
MAX_SCHEDULE_DAYS = 30

BIZ_COMMANDS = {
    ".axclerate", ".axstop", ".axpause", ".axresume", ".shh", ".mute", ".panic", ".afk", ".back",
    ".afkhere", ".later", ".save", ".del", ".tr", ".block", ".nolinks", ".antispam", ".kw", ".snip",
}
_RESERVED = {c[1:] for c in BIZ_COMMANDS}

_AX_JOBS = {}                 # job id -> AxJob
_AX_BY_CHAT = {}              # (connection_id, chat_id) -> AxJob
_AX_IDS = itertools.count(1)

# In-memory copies so the delete path never waits for the database.
_SHH = {}                     # (cid, chat_id) -> {"until","words","media","log","label"}
_RULES = {}                   # (cid, chat_id) -> {"words","nolinks","antispam"}
_CONN_OWNER = {}              # connection_id -> owner user id
_FLOOD = {}                   # (cid, chat_id) -> recent message times
_AFK_LAST = {}                # (cid, chat_id) -> last away reply
_KW_LAST = {}                 # (cid, chat_id, keyword) -> last reply
_KW = {}                      # owner id -> [(keyword, reply)]
_UPSELL_LAST = {}             # owner id -> last "unlock premium" notice
_TRIGGERS = {}                # owner id -> set of custom trigger words


def load_runtime_state():
    _SHH.clear()
    _RULES.clear()
    _TRIGGERS.clear()
    with db() as con:
        for r in con.execute("SELECT * FROM shh_chats"):
            _SHH[(r["connection_id"], r["chat_id"])] = {
                "until": r["until"], "words": json.loads(r["allow_words"] or "[]"),
                "media": set(json.loads(r["allow_media"] or "[]")), "log": bool(r["log"]),
                "label": r["label"]}
        for r in con.execute("SELECT * FROM chat_rules"):
            _RULES[(r["connection_id"], r["chat_id"])] = {
                "words": json.loads(r["block_words"] or "[]"),
                "nolinks": bool(r["no_links"]), "antispam": bool(r["antispam"])}
        for r in con.execute("SELECT connection_id, owner_id FROM business_connections WHERE is_enabled=1"):
            _CONN_OWNER[r["connection_id"]] = r["owner_id"]
        for r in con.execute("SELECT user_id, word FROM triggers"):
            _TRIGGERS.setdefault(r["user_id"], set()).add(r["word"])


def conn_owner(cid):
    owner = _CONN_OWNER.get(cid)
    if owner is None:
        conn = get_connection(cid)
        if conn:
            owner = _CONN_OWNER[cid] = conn["owner_id"]
    return owner


async def _delete_biz(conn_id, ids):
    for _ in range(2):
        try:
            await bot.delete_business_messages(business_connection_id=conn_id, message_ids=ids)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except Exception:
            logging.exception("delete_business_messages failed (needs the 'Manage messages' permission)")
            return False
    return False


async def _send_as_owner(cid, chat_id, text):
    return await bot.send_message(chat_id, text, business_connection_id=cid, parse_mode=None)


async def _edit_own(message, cid, text):
    """Turn the owner's own message into `text`; if Telegram refuses, delete it and send anew."""
    try:
        await bot.edit_message_text(text, business_connection_id=cid, chat_id=message.chat.id,
                                    message_id=message.message_id, parse_mode=None)
        return message.message_id
    except Exception:
        logging.exception("edit of own message failed, sending a new one")
        await _delete_biz(cid, [message.message_id])
        sent = await _send_as_owner(cid, message.chat.id, text)
        return sent.message_id


# ---------------------------------------------------------------------------------
# Repeater (.Axclerate)
# ---------------------------------------------------------------------------------
def ax_get(uid):
    with db() as con:
        r = con.execute("SELECT * FROM ax_config WHERE user_id=?", (uid,)).fetchone()
    if not r:
        return {"messages": [], "interval": 5.0, "interval_max": None, "count": 10}
    return {"messages": json.loads(r["messages"]), "interval": float(r["interval"]),
            "interval_max": r["interval_max"], "count": int(r["count"])}


def ax_save(uid, **changes):
    cfg = ax_get(uid)
    cfg.update(changes)
    with db() as con:
        con.execute(
            "INSERT INTO ax_config(user_id, messages, interval, interval_max, count) VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET messages=excluded.messages, interval=excluded.interval, "
            "interval_max=excluded.interval_max, count=excluded.count",
            (uid, json.dumps(cfg["messages"]), cfg["interval"], cfg["interval_max"], cfg["count"]))


def ax_preset_get(uid, name):
    with db() as con:
        r = con.execute("SELECT * FROM ax_presets WHERE user_id=? AND name=?", (uid, name.lower())).fetchone()
    if not r:
        return None
    return {"messages": json.loads(r["messages"]), "interval": float(r["interval"]),
            "interval_max": r["interval_max"], "count": int(r["count"])}


def ax_preset_names(uid):
    with db() as con:
        return [r["name"] for r in con.execute("SELECT name FROM ax_presets WHERE user_id=? ORDER BY name", (uid,))]


def ax_preset_save(uid, name):
    cfg = ax_get(uid)
    with db() as con:
        con.execute(
            "INSERT INTO ax_presets(user_id, name, messages, interval, interval_max, count) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(user_id, name) DO UPDATE SET messages=excluded.messages, interval=excluded.interval, "
            "interval_max=excluded.interval_max, count=excluded.count",
            (uid, name, json.dumps(cfg["messages"]), cfg["interval"], cfg["interval_max"], cfg["count"]))


def _fmt(n):
    return f"{n:g}"


def fmt_interval(lo, hi):
    return f"{_fmt(lo)}-{_fmt(hi)}s" if hi else f"{_fmt(lo)}s"


def parse_interval(text):
    """'0.5' -> (0.5, None)   '0.5-2' -> (0.5, 2.0): a random delay between the two."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*[-–]\s*(\d+(?:\.\d+)?))?\s*s?", (text or "").strip().lower())
    if not m:
        return None
    lo = max(AX_MIN_INTERVAL, float(m.group(1)))
    hi = float(m.group(2)) if m.group(2) else None
    if hi is not None:
        hi = max(hi, lo)
        if hi == lo:
            hi = None
    return lo, hi


def _to_count(text):
    return max(1, int(text)) if text and text.isdigit() else None


def _parse_ax(rest, uid):
    """'.Axclerate [preset] [count interval] [msg1 | msg2]' -> (items, lo, hi, count, preset)."""
    cfg = ax_get(uid)
    items, lo, hi, count = cfg["messages"], cfg["interval"], cfg["interval_max"], cfg["count"]
    rest = rest.strip()
    first, _, tail = rest.partition(" ")
    preset = None
    if first and "|" not in first and not first[0].isdigit():
        preset = ax_preset_get(uid, first)
        if preset:
            items, lo, hi, count = preset["messages"], preset["interval"], preset["interval_max"], preset["count"]
            rest = tail.strip()
    parts = rest.split(maxsplit=2)
    if len(parts) >= 2 and _to_count(parts[0]) and parse_interval(parts[1]):
        count = _to_count(parts[0])
        lo, hi = parse_interval(parts[1])
        rest = parts[2] if len(parts) == 3 else ""
    if rest.strip():
        items = [p.strip() for p in rest.split("|") if p.strip()]
    return items, lo, hi, count, (first.lower() if preset else None)


class AxJob:
    def __init__(self, cid, chat, uid, items, lo, hi, count):
        self.id = next(_AX_IDS)
        self.cid, self.chat_id, self.uid = cid, chat.id, uid
        self.chat_html, self.chat_label = chat_link(chat), plain_label(chat)
        self.items, self.lo, self.hi, self.count = items, lo, hi, count
        self.sent = 0
        self.state = "running"
        self.resume = asyncio.Event()
        self.resume.set()
        self.task = None
        self.status_id = None
        self.last_edit = 0.0


_AX_SENDERS = {
    "photo": ("send_photo", "photo"), "video": ("send_video", "video"), "voice": ("send_voice", "voice"),
    "audio": ("send_audio", "audio"), "document": ("send_document", "document"),
    "animation": ("send_animation", "animation"), "sticker": ("send_sticker", "sticker"),
    "video_note": ("send_video_note", "video_note"),
}


async def _ax_send(cid, chat_id, item):
    for _ in range(5):
        try:
            if isinstance(item, str):
                return await bot.send_message(chat_id, item, business_connection_id=cid, parse_mode=None)
            meth, arg = _AX_SENDERS[item["t"]]
            kw = {arg: item["id"]}
            if item.get("c") and item["t"] not in ("sticker", "video_note"):
                kw.update(caption=item["c"], parse_mode=None)
            return await getattr(bot, meth)(chat_id, business_connection_id=cid, **kw)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
    raise RuntimeError("Telegram flood limit")


def _bar(done, total, width=10):
    filled = int(width * done / total) if total else 0
    return "▰" * filled + "▱" * (width - filled)


def ax_status_text(job):
    return "\n".join([
        bq(f"{E('bolt')} {sc('Repeater')} ➛ {sc(job.state.capitalize())}"),
        "",
        row("chat", "Chat", job.chat_html),
        row("box", "Sent", f"{job.sent}/{job.count}  {_bar(job.sent, job.count)}"),
        row("time", "Interval", fmt_interval(job.lo, job.hi)),
        SEP,
        f"{E('warn')} <b>{esc(AX_WARNING)}</b>",
    ])


def ax_job_keyboard(job):
    first = (btn("Resume", "ok", style="success", callback_data=f"axj:resume:{job.id}")
             if job.state == "paused"
             else btn("Pause", "time", callback_data=f"axj:pause:{job.id}"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [first, btn("Stop", "err", style="danger", callback_data=f"axj:stop:{job.id}")]])


async def _ax_progress(job, final=False):
    if job.status_id is None:
        return
    t = time.monotonic()
    if not final and t - job.last_edit < 2.0:
        return
    job.last_edit = t
    try:
        await bot.edit_message_text(ax_status_text(job), chat_id=job.uid, message_id=job.status_id,
                                    reply_markup=None if final else ax_job_keyboard(job))
    except TelegramBadRequest:
        pass
    except Exception:
        logging.exception("repeater status update failed")
    if final:
        task = asyncio.create_task(_delete_later(job.uid, job.status_id, 90))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)


async def _ax_run(job):
    key = (job.cid, job.chat_id)
    try:
        for i in range(job.count):
            await job.resume.wait()
            if is_banned(job.uid):
                break
            await _ax_send(job.cid, job.chat_id, job.items[i % len(job.items)])
            job.sent += 1
            await _ax_progress(job)
            if i < job.count - 1:
                await asyncio.sleep(random.uniform(job.lo, job.hi) if job.hi else job.lo)
        job.state = "finished"
    except asyncio.CancelledError:
        job.state = "stopped"
    except Exception:
        logging.exception("Repeater failed")
        job.state = "failed"
    finally:
        _AX_JOBS.pop(job.id, None)
        if _AX_BY_CHAT.get(key) is job:
            _AX_BY_CHAT.pop(key, None)
        await _ax_progress(job, final=True)
        log_activity(job.uid, "repeater", job.chat_id, job.chat_label,
                     f"{job.state} {job.sent}/{job.count} @ {fmt_interval(job.lo, job.hi)}")


@dp.callback_query(F.data.startswith("axj:"))
async def ax_job_buttons(callback: CallbackQuery):
    _, action, jid = callback.data.split(":")
    job = _AX_JOBS.get(int(jid))
    if not job or job.uid != callback.from_user.id:
        return await callback.answer(sc("This repeater has already ended."), show_alert=False)
    if action == "stop":
        job.task.cancel()
        return await callback.answer(sc("Stopping..."))
    if action == "pause":
        job.resume.clear()
        job.state = "paused"
    elif action == "resume":
        job.state = "running"
        job.resume.set()
    job.last_edit = 0.0
    await _ax_progress(job)
    await callback.answer()


async def cmd_axclerate(message, cid, uid, rest, cmd):
    chat = message.chat
    key = (cid, chat.id)
    items, lo, hi, count, preset = _parse_ax(rest, uid)
    await _delete_biz(cid, [message.message_id])  # the trigger never stays in the chat
    if not items:
        return await notice(uid, f"{E('warn')} {sc('No messages set. Open Repeated Message in the bot menu.')}", ttl=12)
    if key in _AX_BY_CHAT:
        return await notice(uid, f"{E('warn')} {sc('Already running there. Send .Axstop first.')}", ttl=8)
    job = AxJob(cid, chat, uid, items, lo, hi, count)
    _AX_JOBS[job.id] = job
    _AX_BY_CHAT[key] = job
    try:
        status = await bot.send_message(uid, ax_status_text(job), reply_markup=ax_job_keyboard(job))
        job.status_id = status.message_id
    except Exception:
        logging.exception("could not send the repeater status")
    job.task = asyncio.create_task(_ax_run(job))


async def cmd_axctl(message, cid, uid, rest, cmd):
    await _delete_biz(cid, [message.message_id])
    job = _AX_BY_CHAT.get((cid, message.chat.id))
    if not job:
        return await notice(uid, f"{E('warn')} {sc('Nothing is running in that chat.')}", ttl=8)
    if cmd == ".axstop":
        job.task.cancel()
        return
    if cmd == ".axpause":
        job.resume.clear()
        job.state = "paused"
    else:
        job.state = "running"
        job.resume.set()
    job.last_edit = 0.0
    await _ax_progress(job)


# ---------------------------------------------------------------------------------
# Silent chat (.shh) and per-chat rules (.block / .nolinks / .antispam)
# ---------------------------------------------------------------------------------
def shh_save(key):
    cfg = _SHH[key]
    with db() as con:
        con.execute(
            "INSERT INTO shh_chats(connection_id, chat_id, until, allow_words, allow_media, log, label) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(connection_id, chat_id) DO UPDATE SET until=excluded.until, "
            "allow_words=excluded.allow_words, allow_media=excluded.allow_media, log=excluded.log, "
            "label=excluded.label",
            (key[0], key[1], cfg["until"], json.dumps(cfg["words"]), json.dumps(sorted(cfg["media"])),
             int(cfg["log"]), cfg["label"]))


def shh_drop(key):
    _SHH.pop(key, None)
    with db() as con:
        con.execute("DELETE FROM shh_chats WHERE connection_id=? AND chat_id=?", key)


def rules_save(key):
    rules = _RULES.get(key)
    with db() as con:
        if not rules or not (rules["words"] or rules["nolinks"] or rules["antispam"]):
            _RULES.pop(key, None)
            con.execute("DELETE FROM chat_rules WHERE connection_id=? AND chat_id=?", key)
        else:
            con.execute(
                "INSERT INTO chat_rules(connection_id, chat_id, block_words, no_links, antispam) VALUES(?,?,?,?,?) "
                "ON CONFLICT(connection_id, chat_id) DO UPDATE SET block_words=excluded.block_words, "
                "no_links=excluded.no_links, antispam=excluded.antispam",
                (key[0], key[1], json.dumps(rules["words"]), int(rules["nolinks"]), int(rules["antispam"])))


def _split_words(text):
    return [w.strip().casefold() for w in re.split(r"[|,]", text or "") if w.strip()]


def shh_status_text(key, chat_html):
    cfg = _SHH.get(key)
    if not cfg:
        return f"{E('lock')} {sc('Shh is now')} <b>{sc('off')}</b> ➛ {chat_html}"
    ends = fmt_local(cfg["until"], conn_owner(key[0])) if cfg["until"] else sc("never")
    return "\n".join([
        f"{E('lock')} {sc('Shh is now')} <b>{sc('ON')}</b> ➛ {chat_html}",
        row("time", "Ends", ends),
        row("ok", "Allowed Words", esc(", ".join(cfg["words"])) or sc("none")),
        row("camera", "Allowed Media", esc(", ".join(sorted(cfg["media"]))) or sc("none")),
        row("pin", "Log", sc("on") if cfg["log"] else sc("off")),
    ])


async def cmd_shh(message, cid, uid, rest, cmd):
    chat = message.chat
    key = (cid, chat.id)
    await _delete_biz(cid, [message.message_id])
    args = rest.split(maxsplit=1)
    sub = args[0].lower() if args else ""
    tail = args[1] if len(args) > 1 else ""
    link = chat_link(chat)
    cfg = _SHH.get(key)

    def ensure():
        nonlocal cfg
        if cfg is None:
            cfg = _SHH[key] = {"until": None, "words": [], "media": set(), "log": False,
                               "label": plain_label(chat)}
        return cfg

    if sub in ("", "off", "stop"):
        if cfg or sub == "":
            if cfg:
                shh_drop(key)
                log_activity(uid, "shh off", chat.id, plain_label(chat))
            else:
                ensure()
                shh_save(key)
                log_activity(uid, "shh on", chat.id, plain_label(chat))
        return await notice(uid, shh_status_text(key, link), ttl=12)
    duration = parse_duration(sub)
    if duration is not None:
        ensure()["until"] = time.time() + duration
        shh_save(key)
        log_activity(uid, "shh on", chat.id, plain_label(chat), f"for {sub}")
    elif sub == "allow":
        ensure()
        cfg["words"] = [] if tail.strip().lower() == "clear" else _split_words(tail)
        if tail.strip().lower() == "clear":
            cfg["media"] = set()
        shh_save(key)
    elif sub == "allowmedia":
        ensure()
        wanted = {w for w in tail.lower().replace(",", " ").split() if w in MEDIA_KINDS}
        cfg["media"] = wanted
        shh_save(key)
    elif sub == "log":
        if not has_pro(uid):
            return await notice(uid, f"{E('lock')} {sc('The shh log is a premium feature (your free trial may have ended).')}", ttl=10)
        ensure()["log"] = not cfg["log"]
        shh_save(key)
    else:
        return await notice(
            uid, "<code>.shh</code> · <code>.shh 30m</code> · <code>.shh allow word | word</code> · "
                 "<code>.shh allowmedia photo voice</code> · <code>.shh log</code> · <code>.shh off</code>", ttl=15)
    await notice(uid, shh_status_text(key, link), ttl=12)


def _shh_should_delete(m, cfg):
    if msg_kind(m) in cfg["media"]:
        return False
    if cfg["words"]:
        text = (m.text or m.caption or "").casefold()
        if any(w in text for w in cfg["words"]):
            return False
    return True


async def _shh_log(owner, message):
    try:
        if has_pro(owner):
            header = (f"{E('pin')} <b>{sc('Shh log')}</b> ➛ {name_link(message.from_user.id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)}")
            await send_payload(owner, msg_payload(message), header)
    except Exception:
        logging.exception("shh log failed")


_LINK_RE = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/)", re.I)


def _has_link(m):
    for ents in (m.entities, m.caption_entities):
        for e in ents or []:
            if e.type in ("url", "text_link"):
                return True
    return bool(_LINK_RE.search(m.text or m.caption or ""))


def _is_flooding(key):
    t = time.monotonic()
    recent = _FLOOD.setdefault(key, deque(maxlen=ANTISPAM_LIMIT + 1))
    recent.append(t)
    return len(recent) > ANTISPAM_LIMIT and t - recent[0] <= ANTISPAM_WINDOW


def _rule_hit(m, rules, key):
    text = (m.text or m.caption or "").casefold()
    if rules["words"] and any(w in text for w in rules["words"]):
        return "word"
    if rules["nolinks"] and _has_link(m):
        return "link"
    if rules["antispam"] and _is_flooding(key):
        return "spam"
    return None


async def cmd_block(message, cid, uid, rest, cmd):
    key = (cid, message.chat.id)
    await _delete_biz(cid, [message.message_id])
    rules = _RULES.setdefault(key, {"words": [], "nolinks": False, "antispam": False})
    arg = rest.strip()
    if arg.lower() in ("off", "clear"):
        rules["words"] = []
    elif arg:
        rules["words"] = _split_words(arg)
    rules_save(key)
    shown = ", ".join(_RULES.get(key, {}).get("words", []))
    await notice(uid, f"{E('ban')} {sc('Blocked words')} ➛ {esc(shown) or sc('none')} ({chat_link(message.chat)})", ttl=10)


async def cmd_rule_toggle(message, cid, uid, rest, cmd):
    key = (cid, message.chat.id)
    await _delete_biz(cid, [message.message_id])
    rules = _RULES.setdefault(key, {"words": [], "nolinks": False, "antispam": False})
    field = "nolinks" if cmd == ".nolinks" else "antispam"
    rules[field] = not rules[field]
    state = rules[field]
    rules_save(key)
    name = "Link filter" if field == "nolinks" else "Flood filter"
    await notice(uid, f"{E('ban')} {sc(name)} ➛ <b>{sc('ON' if state else 'off')}</b> ({chat_link(message.chat)})", ttl=10)


# ---------------------------------------------------------------------------------
# Call history (always on, every connected chat)
# ---------------------------------------------------------------------------------
def _call_of(m):
    """Bot API does not model calls, so read the raw payload."""
    raw = getattr(m, "model_extra", None) or {}
    return raw.get("call")


def _is_call(m):
    return bool(_call_of(m))


async def record_call(message, call):
    cid = message.business_connection_id
    owner = conn_owner(cid)
    if owner is None:
        return
    call = call if isinstance(call, dict) else {}
    direction = str(call.get("status") or call.get("type") or "call").lower()
    duration = call.get("duration")
    label = plain_label(message.chat)
    with db() as con:
        con.execute("INSERT INTO call_log(user_id, chat_id, chat_label, direction, duration, ts) "
                    "VALUES(?,?,?,?,?,?)", (owner, message.chat.id, label, direction, duration, time.time()))
    try:
        with use_lang(owner):
            lines = [f"{E('chat')} <b>{sc('Call')}</b>",
                     row("user", "From", chat_link(message.chat)),
                     row("ok", "Type", sc(direction.capitalize()))]
            if duration:
                lines.append(row("time", "Duration", f"{duration // 60:02d}:{duration % 60:02d}"))
            await bot.send_message(owner, "\n".join(lines))
    except Exception:
        logging.exception("could not deliver a call record")
    log_activity(owner, "call", message.chat.id, label, direction)


@dp.business_message(_is_call)
async def on_call(message: Message):
    await record_call(message, _call_of(message))


# ---------------------------------------------------------------------------------
# Deleted-message saver (premium / free trial, private chats only)
# ---------------------------------------------------------------------------------
_DOT_WORD = re.compile(r"^\.[A-Za-z0-9_]{1,12}$")


def looks_like_command(m):
    """Bot commands (.shh, .later, a snippet like .price, /start ...) are never saved. If the other
    person runs the same bot, a command we delete would otherwise be sent to THEM as 'deleted'."""
    text = (m.text or "").strip()
    if not text:
        return False
    first = text.split(maxsplit=1)[0].lower()
    return first in BIZ_COMMANDS or first.startswith("/") or bool(_DOT_WORD.match(text))


def cache_business_message(m, owner):
    """Keep a copy of what the OTHER person wrote, so it can be sent to you if they delete it."""
    if m.chat.type != "private" or looks_like_command(m) or not deleted_saver_active(owner):
        return
    p = msg_payload(m)
    u = m.from_user
    with db() as con:
        con.execute(
            "INSERT OR REPLACE INTO msg_cache(connection_id, chat_id, message_id, sender_id, sender_name, "
            "sender_username, kind, text, file_id, sent_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (m.business_connection_id, m.chat.id, m.message_id, u.id, u.full_name, u.username,
             p["kind"], p["text"], p["file_id"], now().isoformat()))


def _is_incoming(m):
    """A message in a connected chat written by the OTHER person (not the account owner)."""
    cid = m.business_connection_id
    if not cid or not m.from_user:
        return False
    owner = conn_owner(cid)
    return owner is not None and m.from_user.id != owner


@dp.business_message(_is_incoming)
async def incoming_guard(message: Message):
    """Everything the other person writes passes here: shh first (as fast as possible), then
    block rules, then the deleted-message cache, keyword replies and the away reply."""
    if _is_call(message):
        return                              # calls are handled by on_call
    cid = message.business_connection_id
    key = (cid, message.chat.id)
    cfg = _SHH.get(key)
    if cfg is not None:
        if cfg["until"] and time.time() > cfg["until"]:
            shh_drop(key)                       # timer ran out (the housekeeping loop tells you)
        else:
            if _shh_should_delete(message, cfg):
                owner = conn_owner(cid)
                if cfg["log"]:
                    _TASKS.add(t := asyncio.create_task(_shh_log(owner, message)))
                    t.add_done_callback(_TASKS.discard)
                await _delete_biz(cid, [message.message_id])
            return                              # allowed messages in a shh chat are left alone
    owner = conn_owner(cid)
    rules = _RULES.get(key)
    if rules:
        why = _rule_hit(message, rules, key)
        if why:
            await _delete_biz(cid, [message.message_id])
            return
    if message.chat.type != "private" or message.from_user.is_bot:
        return
    owner_row = get_user(owner)
    if not owner_row or owner_row["banned"]:
        return
    try:
        cache_business_message(message, owner)
    except Exception:
        logging.exception("could not cache message")
    await auto_replies(message, owner)


def _saver_header(owner, emoji, title, row_):
    sent_at = datetime.fromisoformat(row_["sent_at"]).timestamp()
    return "\n".join([
        f"{E(emoji)} <b>{sc(title)}</b>",
        row("user", "From", name_link(row_["sender_id"], row_["sender_username"], row_["sender_name"])),
        row("time", "Sent", fmt_local(sent_at, owner)),
    ])


@dp.edited_business_message(_is_incoming)
async def incoming_edited(message: Message):
    """The other person edited a message we saw: send you the old and the new version."""
    cid = message.business_connection_id
    if (cid, message.chat.id) in _SHH or message.chat.type != "private":
        return
    owner = conn_owner(cid)
    if looks_like_command(message) or not deleted_saver_active(owner):
        return
    with db() as con:
        old = con.execute("SELECT * FROM msg_cache WHERE connection_id=? AND chat_id=? AND message_id=?",
                          (cid, message.chat.id, message.message_id)).fetchone()
        if not old:
            return                      # never saw the original (or it was a command): nothing to compare
        new = msg_payload(message)
        media_changed = (old["kind"], old["file_id"]) != (new["kind"], new["file_id"])
        text_changed = (old["text"] or "") != (new["text"] or "")
        if not (media_changed or text_changed):
            return
        con.execute("UPDATE msg_cache SET kind=?, text=?, file_id=? WHERE connection_id=? AND chat_id=? AND message_id=?",
                    (new["kind"], new["text"], new["file_id"], cid, message.chat.id, message.message_id))
    try:
        with use_lang(owner):
            if media_changed:
                await send_payload(owner, {"kind": old["kind"], "text": old["text"], "file_id": old["file_id"]},
                                   _saver_header(owner, "pin", "Edited message", old) + "\n" + f"{E('box')} <b>{sc('Original')}</b>")
                await send_payload(owner, new, _saver_header(owner, "pin", "Edited message", old) + "\n" + f"{E('ok')} <b>{sc('Now')}</b>")
            else:
                body = "\n".join([_saver_header(owner, "pin", "Edited message", old),
                                  row("pin", "Before", esc((old["text"] or "")[:1500])),
                                  row("ok", "After", esc((new["text"] or "")[:1500]))])
                await bot.send_message(owner, body)
        log_activity(owner, "edited saved", message.chat.id, plain_label(message.chat))
    except Exception:
        logging.exception("could not deliver an edited message")


@dp.deleted_business_messages()
async def on_deleted(event: BusinessMessagesDeleted):
    cid = event.business_connection_id
    owner = conn_owner(cid)
    if owner is None or event.chat.type != "private":
        return
    ids = list(event.message_ids)
    marks = ",".join("?" * len(ids))
    with db() as con:
        rows = con.execute(
            f"SELECT * FROM msg_cache WHERE connection_id=? AND chat_id=? AND message_id IN ({marks}) "
            f"ORDER BY message_id", (cid, event.chat.id, *ids)).fetchall()
        con.execute(f"DELETE FROM msg_cache WHERE connection_id=? AND chat_id=? AND message_id IN ({marks})",
                    (cid, event.chat.id, *ids))
    if not rows:
        return
    with use_lang(owner):
        if not has_pro(owner):
            if time.time() - _UPSELL_LAST.get(owner, 0) > 6 * 3600:
                _UPSELL_LAST[owner] = time.time()
                await notice(owner, "\n".join([
                    bq(f"{E('lock')} {sc('A message was deleted in one of your chats')}"),
                    sc("Your free trial has ended. Premium saves deleted messages for you.")]),
                    ttl=60, reply_markup=back_keyboard([btn("Buy Premium", "pay", style="primary", callback_data="buy")]))
            return
        for r in rows:
            try:
                sent_at = datetime.fromisoformat(r["sent_at"]).timestamp()
                header = "\n".join([
                    f"{E('trash')} <b>{sc('Deleted message')}</b>",
                    row("user", "From", name_link(r["sender_id"], r["sender_username"], r["sender_name"])),
                    row("time", "Sent", fmt_local(sent_at, owner)),
                ])
                await send_payload(owner, {"kind": r["kind"], "text": r["text"], "file_id": r["file_id"]}, header)
            except Exception:
                logging.exception("could not deliver a deleted message")
        log_activity(owner, "deleted saved", event.chat.id, plain_label(event.chat), f"{len(rows)} message(s)")


# ---------------------------------------------------------------------------------
# Away auto-reply (.afk), keyword replies, snippets
# ---------------------------------------------------------------------------------
def afk_get(uid):
    with db() as con:
        r = con.execute("SELECT * FROM afk_config WHERE user_id=?", (uid,)).fetchone()
    if not r:
        return {"enabled": 0, "message": None, "until": None, "sched_on": 0, "start_min": None, "end_min": None}
    return {k: r[k] for k in ("enabled", "message", "until", "sched_on", "start_min", "end_min")}


def afk_set(uid, **changes):
    cfg = afk_get(uid)
    cfg.update(changes)
    with db() as con:
        con.execute(
            "INSERT INTO afk_config(user_id, enabled, message, until, sched_on, start_min, end_min) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=excluded.enabled, "
            "message=excluded.message, until=excluded.until, sched_on=excluded.sched_on, "
            "start_min=excluded.start_min, end_min=excluded.end_min",
            (uid, cfg["enabled"], cfg["message"], cfg["until"], cfg["sched_on"], cfg["start_min"], cfg["end_min"]))


def afk_manual_active(cfg):
    return bool(cfg["enabled"]) and (not cfg["until"] or cfg["until"] > time.time())


def afk_schedule_active(cfg, uid):
    if not cfg["sched_on"] or cfg["start_min"] is None or cfg["end_min"] is None:
        return False
    local = now() + timedelta(minutes=user_tz(uid))
    cur, start, end = local.hour * 60 + local.minute, cfg["start_min"], cfg["end_min"]
    return start <= cur < end if start <= end else (cur >= start or cur < end)


def afk_active(uid):
    cfg = afk_get(uid)
    return afk_manual_active(cfg) or afk_schedule_active(cfg, uid)


def afk_text_for(cid, chat_id, uid):
    with db() as con:
        r = con.execute("SELECT message FROM afk_chat WHERE connection_id=? AND chat_id=?", (cid, chat_id)).fetchone()
    return r["message"] if r else (afk_get(uid)["message"] or AFK_DEFAULT_TEXT)


def kw_list(uid):
    if uid not in _KW:
        with db() as con:
            _KW[uid] = [(r["keyword"], r["reply"]) for r in con.execute(
                "SELECT keyword, reply FROM kw_rules WHERE user_id=? ORDER BY length(keyword) DESC", (uid,))]
    return _KW[uid]


def kw_add(uid, keyword, reply):
    keyword = keyword.strip().casefold()
    with db() as con:
        exists = con.execute("SELECT 1 FROM kw_rules WHERE user_id=? AND keyword=?", (uid, keyword)).fetchone()
        n = con.execute("SELECT COUNT(*) n FROM kw_rules WHERE user_id=?", (uid,)).fetchone()["n"]
        if not exists and n >= MAX_KEYWORDS:
            return False
        con.execute("INSERT INTO kw_rules(user_id, keyword, reply) VALUES(?,?,?) "
                    "ON CONFLICT(user_id, keyword) DO UPDATE SET reply=excluded.reply",
                    (uid, keyword, reply.strip()[:4000]))
    _KW.pop(uid, None)
    return True


def kw_remove(uid, keyword=None, rule_id=None):
    with db() as con:
        if rule_id is not None:
            n = con.execute("DELETE FROM kw_rules WHERE user_id=? AND id=?", (uid, rule_id)).rowcount
        else:
            n = con.execute("DELETE FROM kw_rules WHERE user_id=? AND keyword=?", (uid, keyword.strip().casefold())).rowcount
    _KW.pop(uid, None)
    return n


_NAME_RE = re.compile(r"^[a-z0-9_]{1,12}$")


def snippet_get(uid, name):
    with db() as con:
        r = con.execute("SELECT text FROM snippets WHERE user_id=? AND name=?", (uid, name)).fetchone()
    return r["text"] if r else None


def snippets_list(uid):
    with db() as con:
        return [(r["name"], r["text"]) for r in con.execute(
            "SELECT name, text FROM snippets WHERE user_id=? ORDER BY name", (uid,))]


def snippet_add(uid, name, text):
    name = name.strip().lstrip(".").lower()
    if not _NAME_RE.match(name):
        return "name"
    if name in _RESERVED:
        return "reserved"
    with db() as con:
        exists = con.execute("SELECT 1 FROM snippets WHERE user_id=? AND name=?", (uid, name)).fetchone()
        n = con.execute("SELECT COUNT(*) n FROM snippets WHERE user_id=?", (uid,)).fetchone()["n"]
        if not exists and n >= MAX_SNIPPETS:
            return "limit"
        con.execute("INSERT INTO snippets(user_id, name, text) VALUES(?,?,?) "
                    "ON CONFLICT(user_id, name) DO UPDATE SET text=excluded.text", (uid, name, text.strip()[:4000]))
    return "ok"


def snippet_remove(uid, name):
    with db() as con:
        return con.execute("DELETE FROM snippets WHERE user_id=? AND name=?", (uid, name.strip().lstrip(".").lower())).rowcount


async def auto_replies(message, owner):
    """Keyword replies first; otherwise the away reply (once per chat per cooldown)."""
    cid, chat_id = message.business_connection_id, message.chat.id
    text = message.text or message.caption or ""
    if text:
        low = text.casefold()
        for keyword, reply in kw_list(owner):
            if keyword in low:
                k, t = (cid, chat_id, keyword), time.time()
                if t - _KW_LAST.get(k, 0) >= KW_COOLDOWN:
                    _KW_LAST[k] = t
                    await _send_as_owner(cid, chat_id, reply)
                    log_activity(owner, "keyword reply", chat_id, plain_label(message.chat), keyword)
                return
    if afk_active(owner):
        k, t = (cid, chat_id), time.time()
        if t - _AFK_LAST.get(k, 0) >= AFK_COOLDOWN:
            _AFK_LAST[k] = t
            await _send_as_owner(cid, chat_id, afk_text_for(cid, chat_id, owner))
            log_activity(owner, "away reply", chat_id, plain_label(message.chat))


async def cmd_afk(message, cid, uid, rest, cmd):
    await _delete_biz(cid, [message.message_id])
    if cmd == ".back" or rest.strip().lower() in ("off", "stop"):
        afk_set(uid, enabled=0, until=None)
        return await notice(uid, f"{E('ok')} {sc('Away mode is off.')}", ttl=8)
    first, _, tail = rest.strip().partition(" ")
    duration = parse_duration(first)
    text = tail.strip() if duration is not None else rest.strip()
    changes = {"enabled": 1, "until": (time.time() + duration) if duration else None}
    if text:
        changes["message"] = text[:1000]
    afk_set(uid, **changes)
    until = f" ({sc('until')} {fmt_local(changes['until'], uid)})" if changes["until"] else ""
    await notice(uid, f"{E('ok')} {sc('Away mode is on')}{until}\n{esc(afk_text_for(cid, message.chat.id, uid))}", ttl=12)


async def cmd_afkhere(message, cid, uid, rest, cmd):
    await _delete_biz(cid, [message.message_id])
    with db() as con:
        if rest.strip().lower() in ("off", "clear", ""):
            con.execute("DELETE FROM afk_chat WHERE connection_id=? AND chat_id=?", (cid, message.chat.id))
            return await notice(uid, f"{E('ok')} {sc('This chat uses your default away message.')}", ttl=8)
        con.execute("INSERT INTO afk_chat(connection_id, chat_id, message) VALUES(?,?,?) "
                    "ON CONFLICT(connection_id, chat_id) DO UPDATE SET message=excluded.message",
                    (cid, message.chat.id, rest.strip()[:1000]))
    await notice(uid, f"{E('ok')} {sc('Away message for')} {chat_link(message.chat)} ➛ {esc(rest.strip())}", ttl=10)


async def cmd_kw(message, cid, uid, rest, cmd):
    await _delete_biz(cid, [message.message_id])
    args = rest.split(maxsplit=1)
    sub, tail = (args[0].lower() if args else "list"), (args[1] if len(args) > 1 else "")
    if sub == "add":
        keyword, sep, reply = tail.partition("|")
        if not sep or not keyword.strip() or not reply.strip():
            return await notice(uid, "<code>.kw add price | It costs ₹99</code>", ttl=10)
        if not kw_add(uid, keyword, reply):
            return await notice(uid, f"{E('warn')} {sc('You already have')} {MAX_KEYWORDS} {sc('keyword rules.')}", ttl=8)
        return await notice(uid, f"{E('ok')} {sc('Keyword saved')} ➛ <code>{esc(keyword.strip())}</code>", ttl=8)
    if sub in ("del", "remove"):
        n = kw_remove(uid, keyword=tail)
        return await notice(uid, f"{E('ok') if n else E('warn')} {sc('Removed') if n else sc('Not found')}", ttl=8)
    rules = kw_list(uid)
    body = "\n".join(f"<code>{esc(k)}</code> ➛ {esc(r[:60])}" for k, r in rules) or sc("No keyword rules yet.")
    await notice(uid, f"{bq(E('pin') + ' ' + sc('Keyword replies'))}\n\n{body}", ttl=30)


async def cmd_snip(message, cid, uid, rest, cmd):
    await _delete_biz(cid, [message.message_id])
    args = rest.split(maxsplit=1)
    sub, tail = (args[0].lower() if args else "list"), (args[1] if len(args) > 1 else "")
    if sub == "add":
        name, sep, text = tail.partition("|")
        if not sep or not text.strip():
            return await notice(uid, "<code>.snip add price | It costs ₹99</code>", ttl=10)
        res = snippet_add(uid, name, text)
        msg = {"ok": f"{E('ok')} {sc('Snippet saved. Use it with')} <code>.{esc(name.strip().lstrip('.').lower())}</code>",
               "name": f"{E('warn')} {sc('Name: 1-12 letters, numbers or _')}",
               "reserved": f"{E('warn')} {sc('That name is a command, pick another.')}",
               "limit": f"{E('warn')} {sc('You already have')} {MAX_SNIPPETS} {sc('snippets.')}"}[res]
        return await notice(uid, msg, ttl=10)
    if sub in ("del", "remove"):
        n = snippet_remove(uid, tail)
        return await notice(uid, f"{E('ok') if n else E('warn')} {sc('Removed') if n else sc('Not found')}", ttl=8)
    items = snippets_list(uid)
    body = "\n".join(f"<code>.{esc(n)}</code> ➛ {esc(t[:60])}" for n, t in items) or sc("No snippets yet.")
    await notice(uid, f"{bq(E('pin') + ' ' + sc('Snippets'))}\n\n{body}", ttl=30)


async def cmd_snippet(message, cid, uid, name):
    text = snippet_get(uid, name)
    if text:
        await _edit_own(message, cid, text)


# ---------------------------------------------------------------------------------
# Scheduled messages (.later), self-deleting messages (.del), .save, .tr, .panic
# ---------------------------------------------------------------------------------
def sched_add(kind, uid, cid, chat, run_at, text=None, message_id=None):
    with db() as con:
        return con.execute(
            "INSERT INTO scheduled(kind, owner_id, connection_id, chat_id, label, text, message_id, run_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (kind, uid, cid, chat.id, plain_label(chat), text, message_id, run_at)).lastrowid


def sched_pending(uid):
    with db() as con:
        return con.execute("SELECT * FROM scheduled WHERE owner_id=? AND kind='send' ORDER BY run_at", (uid,)).fetchall()


async def cmd_later(message, cid, uid, rest, cmd):
    parts = rest.split(maxsplit=1)
    await _delete_biz(cid, [message.message_id])
    run_at = parse_when(parts[0], uid) if parts else None
    if run_at is None or len(parts) < 2:
        return await notice(uid, "<code>.later 30m text</code> · <code>.later 2h text</code> · <code>.later 21:30 text</code>", ttl=12)
    if run_at - time.time() > MAX_SCHEDULE_DAYS * 86400:
        return await notice(uid, f"{E('warn')} {sc('Maximum is')} {MAX_SCHEDULE_DAYS} {sc('days.')}", ttl=8)
    if len(sched_pending(uid)) >= MAX_SCHEDULED:
        return await notice(uid, f"{E('warn')} {sc('Too many scheduled messages.')}", ttl=8)
    sched_add("send", uid, cid, message.chat, run_at, text=parts[1])
    log_activity(uid, "scheduled", message.chat.id, plain_label(message.chat), fmt_local(run_at, uid))
    await notice(uid, f"{E('time')} {sc('Scheduled for')} <b>{fmt_local(run_at, uid)}</b> ➛ {chat_link(message.chat)}", ttl=10)


async def cmd_del(message, cid, uid, rest, cmd):
    parts = rest.split(maxsplit=1)
    delay = parse_duration(parts[0]) if parts else None
    reply = message.reply_to_message
    text = parts[1] if len(parts) > 1 else ""
    if delay is None or not (text or (reply and reply.from_user and reply.from_user.id == uid)):
        return await notice(uid, "<code>.del 10s your text</code> · " + sc("or reply to your own message with") + " <code>.del 10s</code>", ttl=12)
    delay = max(1.0, min(delay, 7 * 86400))
    if text:
        target_id = await _edit_own(message, cid, text)
    else:
        await _delete_biz(cid, [message.message_id])
        target_id = reply.message_id
    sched_add("delete", uid, cid, message.chat, time.time() + delay, message_id=target_id)


async def cmd_save(message, cid, uid, rest, cmd):
    await _delete_biz(cid, [message.message_id])
    src = message.reply_to_message
    if not src:
        return await notice(uid, f"{E('warn')} {sc('Reply to a message with .save')}", ttl=8)
    u = src.from_user
    who = name_link(u.id, u.username, u.first_name, u.last_name) if u else chat_link(message.chat)
    header = "\n".join([f"{E('save')} <b>{sc('Saved message')}</b>", row("user", "From", who),
                        row("chat", "Chat", chat_link(message.chat))])
    try:
        await send_payload(uid, msg_payload(src), header)
        log_activity(uid, "saved message", message.chat.id, plain_label(message.chat))
    except Exception:
        logging.exception(".save failed")
        await notice(uid, f"{E('err')} {sc('Could not save that message.')}", ttl=8)


async def translate(text, target):
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get("https://translate.googleapis.com/translate_a/single",
                                   params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t",
                                           "q": text[:3000]}) as resp:
                data = await resp.json(content_type=None)
        return "".join(seg[0] for seg in data[0] if seg and seg[0]) or None
    except Exception:
        logging.exception("translation failed")
        return None


async def cmd_tr(message, cid, uid, rest, cmd):
    parts = rest.split(maxsplit=1)
    lang = parts[0].lower() if parts else ""
    reply = message.reply_to_message
    if not re.fullmatch(r"[a-z]{2,3}(-[a-z]{2,4})?", lang):
        return await notice(uid, "<code>.tr hi how are you</code> · " + sc("or reply to a message with") + " <code>.tr en</code>", ttl=12)
    if len(parts) == 2:
        result = await translate(parts[1], lang)
        if not result:
            return await notice(uid, f"{E('err')} {sc('Translation failed, try again.')}", ttl=8)
        await _edit_own(message, cid, result)
        return log_activity(uid, "translated", message.chat.id, plain_label(message.chat), lang)
    src = (reply.text or reply.caption) if reply else None
    if not src:
        return await notice(uid, f"{E('warn')} {sc('Write text after the language, or reply to a message.')}", ttl=8)
    await _delete_biz(cid, [message.message_id])
    result = await translate(src, lang)
    if not result:
        return await notice(uid, f"{E('err')} {sc('Translation failed, try again.')}", ttl=8)
    await bot.send_message(uid, f"{E('globe')} <b>{sc('Translation')}</b> ➛ {esc(lang)}\n"
                                f"{row('user', 'Chat', chat_link(message.chat))}\n\n{esc(result)}")


async def do_panic(uid):
    jobs = [j for j in _AX_JOBS.values() if j.uid == uid]
    for job in jobs:
        job.task.cancel()
    mine = {c for c, o in _CONN_OWNER.items() if o == uid}
    keys = [k for k in list(_SHH) if k[0] in mine]
    for key in keys:
        shh_drop(key)
    with db() as con:
        scheduled = con.execute("DELETE FROM scheduled WHERE owner_id=? AND kind='send'", (uid,)).rowcount
    afk_set(uid, enabled=0, until=None, sched_on=0)
    log_activity(uid, "panic", detail=f"{len(jobs)} repeaters, {len(keys)} shh, {scheduled} scheduled")
    return len(jobs), len(keys), scheduled


def panic_text(counts):
    jobs, shh, scheduled = counts
    return "\n".join([
        bq(f"{E('ban')} {sc('Panic done')}"), "",
        row("bolt", "Repeaters Stopped", jobs),
        row("lock", "Shh Chats Off", shh),
        row("time", "Scheduled Cleared", scheduled),
        row("chat", "Away Mode", sc("off")),
    ])


async def cmd_panic(message, cid, uid, rest, cmd):
    await _delete_biz(cid, [message.message_id])
    await notice(uid, panic_text(await do_panic(uid)), ttl=15)


@dp.message(Command("panic"))
async def panic_command(message: Message):
    if not message.from_user or is_banned(message.from_user.id):
        return
    await tidy(message)
    await notice(message.chat.id, panic_text(await do_panic(message.from_user.id)), ttl=15)


# ---------------------------------------------------------------------------------
# Command dispatcher for the account owner's own messages
# ---------------------------------------------------------------------------------
_SNIP_RE = re.compile(r"^\.([A-Za-z0-9_]{1,12})$")

_BIZ_HANDLERS = {
    ".axclerate": cmd_axclerate, ".axstop": cmd_axctl, ".axpause": cmd_axctl, ".axresume": cmd_axctl,
    ".shh": cmd_shh, ".mute": cmd_shh, ".panic": cmd_panic, ".afk": cmd_afk, ".back": cmd_afk,
    ".afkhere": cmd_afkhere, ".later": cmd_later, ".save": cmd_save, ".del": cmd_del, ".tr": cmd_tr,
    ".block": cmd_block, ".nolinks": cmd_rule_toggle, ".antispam": cmd_rule_toggle,
    ".kw": cmd_kw, ".snip": cmd_snip,
}


def _biz_special(m: Message):
    if not (m.business_connection_id and m.from_user and m.text):
        return False
    owner = conn_owner(m.business_connection_id)
    if owner is None or m.from_user.id != owner:
        return False
    text = m.text.strip()
    if text.split(maxsplit=1)[0].lower() in BIZ_COMMANDS:
        return True
    hit = _SNIP_RE.match(text)
    return bool(hit and snippet_get(owner, hit.group(1).lower()))


@dp.business_message(_biz_special)
async def business_special(message: Message):
    cid = message.business_connection_id
    owner_id = conn_owner(cid)
    owner = get_user(owner_id)
    if not owner or owner["banned"]:
        return
    words = message.text.strip().split(maxsplit=1)
    cmd = words[0].lower()
    rest = words[1] if len(words) > 1 else ""
    handler = _BIZ_HANDLERS.get(cmd)
    if handler:
        return await handler(message, cid, owner_id, rest, cmd)
    await cmd_snippet(message, cid, owner_id, cmd[1:])


# ---------------------------------------------------------------------------------
# Bot menus for the tools above (buttons + step-by-step prompts)
# ---------------------------------------------------------------------------------
class UFlow(StatesGroup):
    ax_messages = State()
    ax_interval = State()
    ax_count = State()
    ax_media = State()
    ax_preset = State()
    afk_message = State()
    afk_sched = State()
    afk_tz = State()
    kw_add = State()
    sn_add = State()
    trigger_add = State()
    contact = State()


async def open_screen(callback: CallbackQuery, state: FSMContext, text, kb):
    await state.clear()
    await sweep(callback.message.chat.id, keep=callback.message.message_id)
    await safe_edit(callback, text, kb)
    await callback.answer()


async def ask(callback: CallbackQuery, state: FSMContext, new_state, text):
    if is_banned(callback.from_user.id):
        return await callback.answer("Access disabled.", show_alert=True)
    await state.set_state(new_state)
    remember(await callback.message.answer(f"{text}\n{sc('Send /cancel to stop.')}"))
    await callback.answer()


async def flow_end(message: Message, state: FSMContext, text, kb):
    await state.clear()
    chat = message.chat.id
    await tidy(message)
    await sweep(chat)
    await show_panel(chat, text, kb)


async def flow_cancel(message: Message, state: FSMContext):
    await state.clear()
    await tidy(message)
    await sweep(message.chat.id)
    await notice(message.chat.id, f"{E('err')} {sc('Cancelled.')}", ttl=5)


async def flow_retry(message: Message, text):
    await tidy(message)
    await notice(message.chat.id, f"{E('warn')} {text}", ttl=6)


def back_to(callback_data, label="Back"):
    return [btn(label, "home", callback_data=callback_data)]


# ---- Repeated message -------------------------------------------------------------
def ax_item_text(item):
    if isinstance(item, str):
        return esc(item[:50])
    cap = (item.get("c") or "")[:30]
    return f"[{item['t']}]" + (f" {esc(cap)}" if cap else "")


def ax_text(uid):
    cfg = ax_get(uid)
    items = cfg["messages"]
    lines = [bq(f"{E('bolt')} {sc('Repeated message')}"), "", row("pin", "Messages", len(items))]
    lines += [f"   {i}. {ax_item_text(m)}" for i, m in enumerate(items[:12], 1)]
    if len(items) > 12:
        lines.append(f"   … +{len(items) - 12}")
    lines += [row("time", "Interval", fmt_interval(cfg["interval"], cfg["interval_max"])),
              row("box", "Count", cfg["count"])]
    presets = ax_preset_names(uid)
    if presets:
        lines.append(row("save", "Presets", esc(", ".join(presets))))
    lines += [
        SEP,
        f"{E('bolt')} {sc('In any chat send:')}",
        "<code>.Axclerate</code>  <code>.Axpause</code>  <code>.Axresume</code>  <code>.Axstop</code>",
        "<code>.Axclerate 10 0.5-2 hi | hello</code>",
        f"{E('warn')} <b>{esc(AX_WARNING)}</b>",
    ]
    return "\n".join(lines)


def ax_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("Set Messages", "pin", callback_data="axmsgs"), btn("Add Media", "camera", callback_data="axmedia")],
        [btn("Interval", "time", callback_data="axint"), btn("Count", "box", callback_data="axcnt")],
        [btn("Presets", "save", callback_data="axpre"), btn("Clear Items", "trash", callback_data="axclr")],
        [btn("Main Menu", "home", callback_data="home")],
    ])


@dp.callback_query(F.data == "axmenu")
async def ax_menu(callback: CallbackQuery, state: FSMContext):
    upsert_user(callback.from_user)
    if is_banned(callback.from_user.id):
        return await callback.answer("Access disabled.", show_alert=True)
    await open_screen(callback, state, ax_text(callback.from_user.id), ax_keyboard())


@dp.callback_query(F.data == "axmsgs")
async def ax_ask_msgs(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, UFlow.ax_messages,
              f"{E('pin')} {sc('Send your messages separated by')} <code>|</code>\n"
              f"{sc('Example:')} <code>hi | hello | hey</code>\n{sc('This replaces the whole list.')}")


@dp.callback_query(F.data == "axmedia")
async def ax_ask_media(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, UFlow.ax_media,
              f"{E('camera')} {sc('Send a photo, sticker, voice note, video or file to add it to the cycle.')}")


@dp.callback_query(F.data == "axint")
async def ax_ask_interval(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, UFlow.ax_interval,
              f"{E('time')} {sc('Send the interval in seconds, minimum')} {_fmt(AX_MIN_INTERVAL)}\n"
              f"{sc('Example:')} <code>0.5</code> {sc('or a random range')} <code>0.5-2</code>")


@dp.callback_query(F.data == "axcnt")
async def ax_ask_count(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, UFlow.ax_count,
              f"{E('box')} {sc('Send how many messages to send, example')} <code>20</code>")


@dp.callback_query(F.data == "axclr")
async def ax_clear(callback: CallbackQuery, state: FSMContext):
    ax_save(callback.from_user.id, messages=[])
    await open_screen(callback, state, ax_text(callback.from_user.id), ax_keyboard())


@dp.message(UFlow.ax_messages)
async def ax_got_msgs(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    msgs = [p.strip()[:4000] for p in (message.text or "").split("|") if p.strip()]
    if not msgs:
        return await flow_retry(message, sc("Please send some text."))
    ax_save(message.from_user.id, messages=msgs)
    await flow_end(message, state, ax_text(message.from_user.id), ax_keyboard())


@dp.message(UFlow.ax_media)
async def ax_got_media(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    p = msg_payload(message)
    if p["kind"] == "other":
        return await flow_retry(message, sc("That type is not supported. Send text, a photo, sticker, voice, video or file."))
    cfg = ax_get(message.from_user.id)
    if p["kind"] == "text":
        cfg["messages"].append(p["text"][:4000])
    else:
        cfg["messages"].append({"t": p["kind"], "id": p["file_id"], "c": (p["text"] or "")[:900]})
    ax_save(message.from_user.id, messages=cfg["messages"])
    await flow_end(message, state, ax_text(message.from_user.id), ax_keyboard())


@dp.message(UFlow.ax_interval)
async def ax_got_interval(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    parsed = parse_interval(message.text or "")
    if not parsed:
        return await flow_retry(message, sc("Send a number like 0.5 or a range like 0.5-2"))
    ax_save(message.from_user.id, interval=parsed[0], interval_max=parsed[1])
    await flow_end(message, state, ax_text(message.from_user.id), ax_keyboard())


@dp.message(UFlow.ax_count)
async def ax_got_count(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    value = _to_count((message.text or "").strip())
    if value is None:
        return await flow_retry(message, sc("Send a whole number, like 20"))
    ax_save(message.from_user.id, count=value)
    await flow_end(message, state, ax_text(message.from_user.id), ax_keyboard())


# presets
def axpre_text(uid):
    lines = [bq(f"{E('save')} {sc('Repeater presets')}"), ""]
    names = ax_preset_names(uid)
    for name in names:
        p = ax_preset_get(uid, name)
        lines.append(f"<code>.Axclerate {esc(name)}</code> ➛ {len(p['messages'])} {sc('items')}, "
                     f"{p['count']} × {fmt_interval(p['interval'], p['interval_max'])}")
    if not names:
        lines.append(sc("No presets yet. Set up your messages, then save them here."))
    lines += [SEP, sc("A preset stores your current messages, interval and count.")]
    return "\n".join(lines)


def axpre_keyboard(uid):
    rows = []
    for name in ax_preset_names(uid):
        rows.append([btn(f"Load {name}", "ok", callback_data=f"axpl:{name}"),
                     btn(f"Delete {name}", "trash", style="danger", callback_data=f"axpd:{name}")])
    rows.append([btn("Save Current As Preset", "save", style="primary", callback_data="axps")])
    rows.append(back_to("axmenu"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "axpre")
async def ax_presets(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    await open_screen(callback, state, axpre_text(uid), axpre_keyboard(uid))


@dp.callback_query(F.data == "axps")
async def ax_preset_ask(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not ax_get(uid)["messages"]:
        return await callback.answer(sc("Add some messages first."), show_alert=True)
    if len(ax_preset_names(uid)) >= MAX_PRESETS:
        return await callback.answer(sc("Too many presets. Delete one first."), show_alert=True)
    await ask(callback, state, UFlow.ax_preset,
              f"{E('save')} {sc('Send a name for this preset (letters, numbers, _ up to 16).')}")


@dp.message(UFlow.ax_preset)
async def ax_preset_name(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    name = (message.text or "").strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,15}", name) or name.isdigit():
        return await flow_retry(message, sc("Use letters, numbers or _ (start with a letter, max 16)."))
    uid = message.from_user.id
    ax_preset_save(uid, name)
    await flow_end(message, state, axpre_text(uid), axpre_keyboard(uid))


@dp.callback_query(F.data.startswith("axpl:"))
async def ax_preset_load(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    preset = ax_preset_get(uid, callback.data.split(":", 1)[1])
    if preset:
        ax_save(uid, **preset)
    await open_screen(callback, state, ax_text(uid), ax_keyboard())


@dp.callback_query(F.data.startswith("axpd:"))
async def ax_preset_delete(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    with db() as con:
        con.execute("DELETE FROM ax_presets WHERE user_id=? AND name=?", (uid, callback.data.split(":", 1)[1]))
    await open_screen(callback, state, axpre_text(uid), axpre_keyboard(uid))


# ---- Deleted messages --------------------------------------------------------------
def del_text(uid):
    row_ = get_user(uid)
    on = bool(row_ and row_["save_deleted"])
    lines = [
        bq(f"{E('trash')} {sc('Deleted message saver')}"), "",
        row("star", "Access", pro_status_text(uid)),
        row("ok" if on else "err", "Saving", sc("On") if on else sc("Off")),
        row("time", "Kept For", f"{DELETED_CACHE_HOURS} {sc('hours')}"),
        SEP,
        sc("When someone deletes a message in your private chats, the bot sends it to you here."),
        sc("It also tells you when they edit a message, with the old and the new text."),
        sc("Text, photos, videos, voice notes, stickers and files are saved."),
        sc("Needs this bot connected in Settings → Business → Chatbots."),
    ]
    if not has_pro(uid):
        lines += [SEP, f"{E('lock')} {sc('Your free trial has ended. Premium unlocks this again.')}"]
    return "\n".join(lines)


def del_keyboard(uid):
    rows = []
    if has_pro(uid):
        on = bool((get_user(uid) or {"save_deleted": 1})["save_deleted"])
        rows.append([btn("Turn Off" if on else "Turn On", "ok" if not on else "err", callback_data="deltoggle")])
    if not is_premium(uid):
        rows.append([btn("Buy Premium", "pay", style="primary", callback_data="buy")])
    rows.append([btn("Main Menu", "home", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "delmenu")
async def del_menu(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    await open_screen(callback, state, del_text(uid), del_keyboard(uid))


@dp.callback_query(F.data == "deltoggle")
async def del_toggle(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    with db() as con:
        con.execute("UPDATE users SET save_deleted=1-save_deleted WHERE user_id=?", (uid,))
    await open_screen(callback, state, del_text(uid), del_keyboard(uid))


# ---- Auto reply (away mode) ---------------------------------------------------------
def afk_text_screen(uid):
    cfg = afk_get(uid)
    manual = afk_manual_active(cfg)
    sched = f"{cfg['start_min'] // 60:02d}:{cfg['start_min'] % 60:02d} - {cfg['end_min'] // 60:02d}:{cfg['end_min'] % 60:02d}" \
        if cfg["start_min"] is not None and cfg["end_min"] is not None else sc("not set")
    lines = [
        bq(f"{E('chat')} {sc('Auto reply (away mode)')}"), "",
        row("ok" if afk_active(uid) else "err", "Status", sc("Replying") if afk_active(uid) else sc("Off")),
        row("bolt", "Manual", sc("on") if manual else sc("off")),
        row("calendar", "Schedule", f"{sched} ({sc('on') if cfg['sched_on'] else sc('off')})"),
        row("globe", "Timezone", fmt_tz(user_tz(uid))),
        row("pin", "Message", esc((cfg["message"] or AFK_DEFAULT_TEXT)[:120])),
        SEP,
        sc("People who message you get this reply once every 6 hours."),
        sc("In a chat:"),
        "<code>.afk 2h in a meeting</code>  <code>.back</code>",
        "<code>.afkhere message</code> " + sc("(only for that chat)"),
    ]
    return "\n".join(lines)


def afk_keyboard(uid):
    manual = afk_manual_active(afk_get(uid))
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("Turn Off Now" if manual else "Turn On Now", "err" if manual else "ok", callback_data="afktoggle")],
        [btn("Set Message", "pin", callback_data="afkmsg"), btn("Schedule", "calendar", callback_data="afksched")],
        [btn("Timezone", "globe", callback_data="afktz")],
        [btn("Main Menu", "home", callback_data="home")],
    ])


@dp.callback_query(F.data == "afkmenu")
async def afk_menu(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    await open_screen(callback, state, afk_text_screen(uid), afk_keyboard(uid))


@dp.callback_query(F.data == "afktoggle")
async def afk_toggle(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if afk_manual_active(afk_get(uid)):
        afk_set(uid, enabled=0, until=None)
    else:
        afk_set(uid, enabled=1, until=None)
    await open_screen(callback, state, afk_text_screen(uid), afk_keyboard(uid))


@dp.callback_query(F.data == "afkmsg")
async def afk_ask_msg(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, UFlow.afk_message, f"{E('pin')} {sc('Send your away message.')}")


@dp.callback_query(F.data == "afksched")
async def afk_ask_sched(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, UFlow.afk_sched,
              f"{E('calendar')} {sc('Send the daily away hours in your timezone.')}\n"
              f"{sc('Example:')} <code>22:00-07:00</code>  {sc('or')} <code>off</code>")


@dp.callback_query(F.data == "afktz")
async def afk_ask_tz(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, UFlow.afk_tz,
              f"{E('globe')} {sc('Send your timezone offset from UTC.')}\n"
              f"{sc('Example:')} <code>+5:30</code>  <code>-4</code>  <code>0</code>")


@dp.message(UFlow.afk_message)
async def afk_got_msg(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    if not message.text:
        return await flow_retry(message, sc("Please send some text."))
    uid = message.from_user.id
    afk_set(uid, message=message.text[:1000])
    await flow_end(message, state, afk_text_screen(uid), afk_keyboard(uid))


@dp.message(UFlow.afk_sched)
async def afk_got_sched(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    uid = message.from_user.id
    text = (message.text or "").strip().lower()
    if text in ("off", "clear"):
        afk_set(uid, sched_on=0, start_min=None, end_min=None)
    else:
        a, _, b = text.partition("-")
        start, end = parse_clock(a), parse_clock(b)
        if start is None or end is None:
            return await flow_retry(message, sc("Use the format 22:00-07:00"))
        afk_set(uid, sched_on=1, start_min=start, end_min=end)
    await flow_end(message, state, afk_text_screen(uid), afk_keyboard(uid))


@dp.message(UFlow.afk_tz)
async def afk_got_tz(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    minutes = parse_tz(message.text or "")
    if minutes is None:
        return await flow_retry(message, sc("Use an offset like +5:30 or -4"))
    uid = message.from_user.id
    with db() as con:
        con.execute("UPDATE users SET tz_offset=? WHERE user_id=?", (minutes, uid))
    await flow_end(message, state, afk_text_screen(uid), afk_keyboard(uid))


# ---- Tools hub ------------------------------------------------------------------------
def tools_text():
    return "\n".join([
        bq(f"{E('tools')} {sc('Tools')}"), "",
        sc("Everything here works inside your own chats through your Business account."),
    ])


def tools_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("Trigger Words", "bolt", callback_data="trgmenu"), btn("Call History", "chat", callback_data="callmenu")],
        [btn("Keyword Replies", "bolt", callback_data="kwmenu"), btn("Snippets", "pin", callback_data="snmenu")],
        [btn("Scheduled", "time", callback_data="schmenu"), btn("Activity", "list", callback_data="actmenu")],
        [btn("Commands", "play", callback_data="helpmenu"), btn("Panic", "ban", style="danger", callback_data="panicbtn")],
        [btn("Main Menu", "home", callback_data="home")],
    ])


@dp.callback_query(F.data == "toolsmenu")
async def tools_menu(callback: CallbackQuery, state: FSMContext):
    await open_screen(callback, state, tools_text(), tools_keyboard())


@dp.callback_query(F.data == "panicbtn")
async def panic_button(callback: CallbackQuery):
    await callback.answer()
    await notice(callback.message.chat.id, panic_text(await do_panic(callback.from_user.id)), ttl=15)


def help_text():
    return "\n".join([
        bq(f"{E('play')} {sc('Commands for your chats')}"), "",
        "<code>.Axclerate</code> <code>.Axpause</code> <code>.Axresume</code> <code>.Axstop</code>",
        "<code>.shh</code> <code>.shh 30m</code> <code>.shh allow w | w</code>",
        "<code>.shh allowmedia photo</code> <code>.shh log</code>",
        "<code>.afk 2h text</code> <code>.back</code> <code>.afkhere text</code>",
        "<code>.later 30m text</code> <code>.later 21:30 text</code>",
        "<code>.save</code> (" + sc("reply") + ")  <code>.del 10s text</code>",
        "<code>.tr hi text</code> <code>.tr en</code> (" + sc("reply") + ")",
        "<code>.block w | w</code> <code>.nolinks</code> <code>.antispam</code>",
        "<code>.kw add word | reply</code> <code>.snip add name | text</code>",
        "<code>.panic</code>",
        SEP,
        f"{E('bolt')} {sc('Archive by replying with')} <code>{TRIGGER_WORD}</code> " + sc("or your own words from Trigger Words."),
        f"{E('lock')} {sc('Only you can use these, and the bot deletes the command message.')}",
        f"{E('warn')} <b>{esc(AX_WARNING)}</b>",
    ])


@dp.callback_query(F.data == "helpmenu")
async def help_menu(callback: CallbackQuery, state: FSMContext):
    await open_screen(callback, state, help_text(), back_keyboard(back_to("toolsmenu")))


# ---- Custom archive commands -----------------------------------------------------------
def trigger_access_text(uid):
    if is_premium(uid):
        return sc("Premium")
    end = cmd_trial_expiry(uid)
    if end:
        return f"{sc('Free Trial')} ({fmt_left(end - now())})"
    return sc("Locked")


def trg_text(uid):
    words = sorted(user_triggers(uid))
    lines = [
        bq(f"{E('bolt')} {sc('Custom commands')}"),
        "",
        row("star", "Access", trigger_access_text(uid)),
        row("ok", "Default", f"<code>{TRIGGER_WORD}</code>"),
    ]
    lines += [row("pin", "Custom", f"<code>{esc(w)}</code>") for w in words]
    lines += [
        row("box", "Slots", f"{len(words)}/{MAX_TRIGGERS}"),
        SEP,
        sc("Reply to any media with one of these words and the copy arrives here."),
        sc("The default word always works, custom ones are a premium feature."),
    ]
    if not is_premium(uid) and cmd_trial_expiry(uid) is None:
        lines += [SEP, f"{E('lock')} {sc('Your 7 day trial has ended. Premium unlocks custom commands.')}"]
    return "\n".join(lines)


def trg_keyboard(uid):
    rows = [[btn(f"Remove {w}", "trash", style="danger", callback_data=f"trgdel:{w}")]
            for w in sorted(user_triggers(uid))]
    if has_custom_triggers(uid) and len(user_triggers(uid)) < MAX_TRIGGERS:
        rows.append([btn("Add Command", "ok", style="success", callback_data="trgadd")])
    if not is_premium(uid):
        rows.append([btn("Buy Premium", "pay", style="primary", callback_data="buy")])
    rows.append(back_to("toolsmenu"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.callback_query(F.data == "trgmenu")
async def trg_menu(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    await open_screen(callback, state, trg_text(uid), trg_keyboard(uid))


@dp.callback_query(F.data == "trgadd")
async def trg_ask(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    if not has_custom_triggers(uid):
        return await callback.answer(sc("Your trial has ended. Premium unlocks custom commands."), show_alert=True)
    if len(user_triggers(uid)) >= MAX_TRIGGERS:
        return await callback.answer(sc("You already have") + f" {MAX_TRIGGERS} " + sc("custom commands."), show_alert=True)
    await ask(callback, state, UFlow.trigger_add,
              f"{E('bolt')} {sc('Send the word to archive with.')}\n"
              f"{sc('Example:')} <code>keep</code>\n{sc('Letters, numbers or _ only.')}")


@dp.message(UFlow.trigger_add)
async def trg_got(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    uid = message.from_user.id
    if not has_custom_triggers(uid):
        return await flow_retry(message, sc("Your trial has ended. Premium unlocks custom commands."))
    res = add_trigger(uid, message.text or "")
    errors = {"format": sc("Letters, numbers or _ only, max 24 characters."),
              "default": sc("That is already the default word, pick another."),
              "limit": sc("You already have") + f" {MAX_TRIGGERS} " + sc("custom commands.")}
    if res != "ok":
        return await flow_retry(message, errors[res])
    await flow_end(message, state, trg_text(uid), trg_keyboard(uid))


@dp.callback_query(F.data.startswith("trgdel:"))
async def trg_delete(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    remove_trigger(uid, callback.data.split(":", 1)[1])
    await open_screen(callback, state, trg_text(uid), trg_keyboard(uid))


# ---- Call history screen ---------------------------------------------------------------
def calls_text(uid):
    with db() as con:
        rows = con.execute("SELECT * FROM call_log WHERE user_id=? ORDER BY ts DESC LIMIT 20", (uid,)).fetchall()
    lines = [bq(f"{E('chat')} {sc('Call history')}"), ""]
    for r in rows:
        dur = f" · {r['duration'] // 60:02d}:{r['duration'] % 60:02d}" if r["duration"] else ""
        lines.append(f"{fmt_local(r['ts'], uid)} · <b>{sc((r['direction'] or 'call').capitalize())}</b> "
                     f"➛ {chat_ref(r['chat_id'], r['chat_label'])}{dur}")
    if not rows:
        lines.append(sc("Nothing yet."))
    lines += [SEP, sc("Every call in your connected chats is recorded here automatically.")]
    return "\n".join(lines)


@dp.callback_query(F.data == "callmenu")
async def call_menu(callback: CallbackQuery, state: FSMContext):
    await open_screen(callback, state, calls_text(callback.from_user.id), back_keyboard(back_to("toolsmenu")))


# keyword replies
def kw_rows(uid):
    with db() as con:
        return con.execute("SELECT id, keyword, reply FROM kw_rules WHERE user_id=? ORDER BY id", (uid,)).fetchall()


def kw_text(uid):
    rows = kw_rows(uid)
    lines = [bq(f"{E('bolt')} {sc('Keyword replies')}"), ""]
    lines += [f"<code>{esc(r['keyword'])}</code> ➛ {esc(r['reply'][:60])}" for r in rows] or [sc("No keyword rules yet.")]
    lines += [SEP, sc("When someone writes a keyword in a private chat, your account answers for you.")]
    return "\n".join(lines)


def kw_keyboard(uid):
    rows = kw_rows(uid)
    buttons = [btn(f"Delete {r['keyword'][:14]}", "trash", callback_data=f"kwdel:{r['id']}") for r in rows]
    kb = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    kb.append([btn("Add Rule", "ok", style="success", callback_data="kwadd")])
    kb.append(back_to("toolsmenu"))
    return InlineKeyboardMarkup(inline_keyboard=kb)


@dp.callback_query(F.data == "kwmenu")
async def kw_menu(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    await open_screen(callback, state, kw_text(uid), kw_keyboard(uid))


@dp.callback_query(F.data == "kwadd")
async def kw_ask(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, UFlow.kw_add,
              f"{E('bolt')} {sc('Send the keyword and the reply, separated by')} <code>|</code>\n"
              f"{sc('Example:')} <code>price | It costs ₹99</code>")


@dp.message(UFlow.kw_add)
async def kw_got(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    keyword, sep, reply = (message.text or "").partition("|")
    if not sep or not keyword.strip() or not reply.strip():
        return await flow_retry(message, sc("Use the format: keyword | reply"))
    uid = message.from_user.id
    if not kw_add(uid, keyword, reply):
        return await flow_retry(message, f"{sc('You already have')} {MAX_KEYWORDS} {sc('keyword rules.')}")
    await flow_end(message, state, kw_text(uid), kw_keyboard(uid))


@dp.callback_query(F.data.startswith("kwdel:"))
async def kw_delete(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    kw_remove(uid, rule_id=int(callback.data.split(":")[1]))
    await open_screen(callback, state, kw_text(uid), kw_keyboard(uid))


# snippets
def sn_text(uid):
    items = snippets_list(uid)
    lines = [bq(f"{E('pin')} {sc('Snippets')}"), ""]
    lines += [f"<code>.{esc(n)}</code> ➛ {esc(t[:60])}" for n, t in items] or [sc("No snippets yet.")]
    lines += [SEP, sc("Type .name in any chat and your message turns into the saved text.")]
    return "\n".join(lines)


def sn_keyboard(uid):
    buttons = [btn(f"Delete .{n}", "trash", callback_data=f"sndel:{n}") for n, _t in snippets_list(uid)]
    kb = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    kb.append([btn("Add Snippet", "ok", style="success", callback_data="snadd")])
    kb.append(back_to("toolsmenu"))
    return InlineKeyboardMarkup(inline_keyboard=kb)


@dp.callback_query(F.data == "snmenu")
async def sn_menu(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    await open_screen(callback, state, sn_text(uid), sn_keyboard(uid))


@dp.callback_query(F.data == "snadd")
async def sn_ask(callback: CallbackQuery, state: FSMContext):
    await ask(callback, state, UFlow.sn_add,
              f"{E('pin')} {sc('Send the name and the text, separated by')} <code>|</code>\n"
              f"{sc('Example:')} <code>price | It costs ₹99</code>")


@dp.message(UFlow.sn_add)
async def sn_got(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    name, sep, text = (message.text or "").partition("|")
    if not sep or not text.strip():
        return await flow_retry(message, sc("Use the format: name | text"))
    uid = message.from_user.id
    res = snippet_add(uid, name, text)
    errors = {"name": sc("Name: 1-12 letters, numbers or _"),
              "reserved": sc("That name is a command, pick another."),
              "limit": f"{sc('You already have')} {MAX_SNIPPETS} {sc('snippets.')}"}
    if res != "ok":
        return await flow_retry(message, errors[res])
    await flow_end(message, state, sn_text(uid), sn_keyboard(uid))


@dp.callback_query(F.data.startswith("sndel:"))
async def sn_delete(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    snippet_remove(uid, callback.data.split(":", 1)[1])
    await open_screen(callback, state, sn_text(uid), sn_keyboard(uid))


# scheduled messages
def sch_text(uid):
    rows = sched_pending(uid)
    lines = [bq(f"{E('time')} {sc('Scheduled messages')}"), ""]
    for r in rows[:15]:
        lines.append(f"{fmt_local(r['run_at'], uid)} ➛ {chat_ref(r['chat_id'], r['label'])}: {esc((r['text'] or '')[:40])}")
    if not rows:
        lines.append(sc("Nothing scheduled."))
    lines += [SEP, "<code>.later 30m text</code>  <code>.later 21:30 text</code>", f"{sc('Timezone')} ➛ {fmt_tz(user_tz(uid))}"]
    return "\n".join(lines)


def sch_keyboard(uid):
    buttons = [btn(f"✖ {fmt_local(r['run_at'], uid)}", callback_data=f"schdel:{r['id']}") for r in sched_pending(uid)[:15]]
    kb = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    kb.append(back_to("toolsmenu"))
    return InlineKeyboardMarkup(inline_keyboard=kb)


@dp.callback_query(F.data == "schmenu")
async def sch_menu(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    await open_screen(callback, state, sch_text(uid), sch_keyboard(uid))


@dp.callback_query(F.data.startswith("schdel:"))
async def sch_delete(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    with db() as con:
        con.execute("DELETE FROM scheduled WHERE id=? AND owner_id=?", (int(callback.data.split(":")[1]), uid))
    await open_screen(callback, state, sch_text(uid), sch_keyboard(uid))


# activity log
def act_text(uid):
    with db() as con:
        rows = con.execute("SELECT * FROM activity WHERE user_id=? ORDER BY ts DESC LIMIT 15", (uid,)).fetchall()
    lines = [bq(f"{E('list')} {sc('Recent activity')}"), ""]
    for r in rows:
        where = f" {chat_ref(r['chat_id'], r['label'])}" if r["chat_id"] else ""
        detail = f" · {esc(r['detail'])}" if r["detail"] else ""
        lines.append(f"{fmt_local(r['ts'], uid)} · <b>{sc(r['kind'])}</b>{where}{detail}")
    if not rows:
        lines.append(sc("Nothing yet."))
    return "\n".join(lines)


@dp.callback_query(F.data == "actmenu")
async def act_menu(callback: CallbackQuery, state: FSMContext):
    await open_screen(callback, state, act_text(callback.from_user.id), back_keyboard(back_to("toolsmenu")))


# ---- Language -----------------------------------------------------------------------------
@dp.callback_query(F.data == "langmenu")
async def lang_menu(callback: CallbackQuery, state: FSMContext):
    cur = get_lang(callback.from_user.id)
    text = "\n".join([bq(f"{E('globe')} {sc('Language')}"), "", sc("Choose your language.")])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [btn(("✓ " if cur == "en" else "") + "English", callback_data="setlang:en"),
         btn(("✓ " if cur == "hi" else "") + "हिन्दी", callback_data="setlang:hi"),
         btn(("✓ " if cur == "hl" else "") + "Hinglish", callback_data="setlang:hl")],
        [btn("Main Menu", "home", callback_data="home")],
    ])
    await open_screen(callback, state, text, kb)


@dp.callback_query(F.data.startswith("setlang:"))
async def lang_set(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split(":")[1]
    if lang not in ("en", "hi", "hl"):
        return await callback.answer()
    set_lang(callback.from_user.id, lang)
    _LANG.set(lang)                      # the very next screen is already in the new language
    uid = callback.from_user.id
    await open_screen(callback, state, home_text(uid, callback.from_user.first_name),
                      home_keyboard(uid == OWNER_ID))


class LangMiddleware(BaseMiddleware):
    """Every update is answered in the language of the person it concerns."""
    async def __call__(self, handler, event, data):
        token = _LANG.set(get_lang(_update_user(event)))
        try:
            return await handler(event, data)
        finally:
            _LANG.reset(token)


def _update_user(update):
    for attr in ("business_message", "edited_business_message"):
        m = getattr(update, attr, None)
        if m:
            return conn_owner(m.business_connection_id)
    deleted = update.deleted_business_messages
    if deleted:
        return conn_owner(deleted.business_connection_id)
    for obj in (update.message, update.callback_query):
        if obj and obj.from_user:
            return obj.from_user.id
    return None


dp.update.outer_middleware(LangMiddleware())


# ---- Contact the admin through the bot (user <-> admin conversation) -------------------
_CONTACT_LAST = {}


@dp.callback_query(F.data == "contact")
async def contact_start(callback: CallbackQuery, state: FSMContext):
    uid = callback.from_user.id
    upsert_user(callback.from_user)
    if is_banned(uid):
        return await callback.answer("Access disabled.", show_alert=True)
    if uid == OWNER_ID:
        return await callback.answer(sc("You are the admin."), show_alert=True)
    await ask(callback, state, UFlow.contact,
              f"{E('mail')} {sc('Write your message for the admin. You can send text, a photo, voice note or file.')}")


@dp.message(UFlow.contact)
async def contact_send(message: Message, state: FSMContext):
    if message.text == "/cancel":
        return await flow_cancel(message, state)
    u = message.from_user
    if is_banned(u.id):
        return await flow_cancel(message, state)
    if time.time() - _CONTACT_LAST.get(u.id, 0) < 10:
        return await flow_retry(message, sc("Please wait a few seconds before sending another message."))
    payload = msg_payload(message)
    if payload["kind"] == "other":
        return await flow_retry(message, sc("That type is not supported. Send text, a photo, sticker, voice, video or file."))
    _CONTACT_LAST[u.id] = time.time()
    chat = message.chat.id
    try:
        with use_lang(OWNER_ID):                       # the admin reads it in the admin's language
            header = "\n".join([
                f"{E('mail')} <b>{sc('New message from a user')}</b>",
                row("user", "From", name_link(u.id, u.username, u.first_name, u.last_name)),
                row("id", "ID", f"<code>{u.id}</code>"),
            ])
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [btn("Reply", "mail", style="success", callback_data=f"creply:{u.id}")]])
            await send_payload(OWNER_ID, payload, header, reply_markup=kb)
    except Exception:
        logging.exception("could not forward a contact message to the admin")
        return await flow_retry(message, sc("Could not reach the admin right now. Please try again later."))
    await state.clear()
    await sweep(chat)
    await notice(chat, f"{E('ok')} {sc('Sent to the admin. The reply will arrive here.')}", ttl=10)


@dp.callback_query(F.data.startswith("creply:"))
async def admin_reply_start(callback: CallbackQuery, state: FSMContext):
    if not await admin_only(callback):
        return
    uid = int(callback.data.split(":")[1])
    await state.set_state(AdminFlow.reply)
    await state.update_data(uid=uid)
    remember(await callback.message.answer(
        f"{E('mail')} {sc('Write your reply to')} {db_user_link(uid)}\n{sc('Send /cancel to stop.')}"))
    await callback.answer()


@dp.message(AdminFlow.reply)
async def admin_reply_send(message: Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        return
    chat = message.chat.id
    if message.text == "/cancel":
        await state.clear()
        await sweep(chat)
        return await notice(chat, f"{E('err')} {sc('Cancelled.')}", ttl=5)
    payload = msg_payload(message)
    if payload["kind"] == "other":
        return await notice(chat, f"{E('warn')} {sc('That type is not supported. Send text, a photo, sticker, voice, video or file.')}", ttl=6)
    uid = (await state.get_data())["uid"]
    await state.clear()
    await sweep(chat)
    try:
        with use_lang(uid):                            # the user reads it in their own language
            header = f"{E('mail')} <b>{sc('Message from the admin')}</b>"
            kb = InlineKeyboardMarkup(inline_keyboard=[[btn("Reply", "mail", style="success", callback_data="contact")]])
            await send_payload(uid, payload, header, reply_markup=kb)
        await notice(chat, f"{E('ok')} {sc('Reply delivered to')} {db_user_link(uid)}", ttl=8)
    except Exception:
        logging.exception("could not deliver the admin reply to %s", uid)
        await notice(chat, f"{E('err')} {sc('Could not deliver to')} {db_user_link(uid)}", ttl=10)


# ---- free trial introduction ----------------------------------------------------------------
def trial_intro_text(uid):
    end = trial_expiry(uid)
    lines = [bq(f"{E('gift')} {sc('New: deleted message saver')}"), "",
             sc("When someone deletes a message in your private chats, the bot sends it to you here."),
             sc("It also tells you when they edit a message, with the old and the new text."),
             sc("Connect this bot in Settings → Business → Chatbots.")]
    if is_premium(uid):
        lines += ["", f"{E('star')} {sc('It is included in your Premium.')}"]
    elif end:
        lines += ["", f"{E('gift')} <b>{sc('Free trial unlocked')}</b>",
                  row("calendar", "Trial Ends", f"{end:%d %b %Y %H:%M} UTC"),
                  f"{E('star')} {sc('Keep it after the trial with Premium.')}"]
    return "\n".join(lines)


async def send_trial_intro(uid):
    """Tell a user about the saver + free trial (once). Returns False if they can't be reached."""
    with db() as con:
        con.execute("UPDATE users SET trial_notified=1 WHERE user_id=?", (uid,))
    try:
        with use_lang(uid):
            await bot.send_message(uid, trial_intro_text(uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [btn("Deleted Messages", "trash", style="primary", callback_data="delmenu")],
                [btn("Main Menu", "home", callback_data="home")]]))
        return True
    except Exception:
        logging.info("could not send the trial intro to %s", uid)
        return False


def trigger_intro_text(uid):
    end = cmd_trial_expiry(uid)
    lines = [bq(f"{E('bolt')} {sc('New: custom archive commands')}"), "",
             sc("You can archive media with your own words now, up to") + f" {MAX_TRIGGERS}.",
             sc("Reply to any media with your word, exactly like the default one."),
             row("ok", "Default", f"<code>{TRIGGER_WORD}</code>")]
    if is_premium(uid):
        lines += ["", f"{E('star')} {sc('It is included in your Premium.')}"]
    elif end:
        lines += ["", f"{E('gift')} <b>{sc('7 day free trial unlocked')}</b>",
                  row("calendar", "Trial Ends", f"{end:%d %b %Y %H:%M} UTC"),
                  f"{E('star')} {sc('Keep it after the trial with Premium.')}"]
    return "\n".join(lines)


async def send_trigger_intro(uid):
    """Tell a user about custom commands (once)."""
    with db() as con:
        con.execute("UPDATE users SET cmd_notified=1 WHERE user_id=?", (uid,))
    try:
        with use_lang(uid):
            await bot.send_message(uid, trigger_intro_text(uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [btn("Trigger Words", "bolt", style="primary", callback_data="trgmenu")],
                [btn("Main Menu", "home", callback_data="home")]]))
        return True
    except Exception:
        logging.info("could not send the trigger intro to %s", uid)
        return False


async def announce_trial_loop():
    """Once after the update: everyone who has not heard about a feature gets the message."""
    await asyncio.sleep(20)
    with db() as con:
        ids = [r["user_id"] for r in con.execute("SELECT user_id FROM users WHERE banned=0 AND trial_notified=0")]
    for uid in ids:
        await send_trial_intro(uid)
        await asyncio.sleep(0.06)  # gentle rate limiting
    logging.info("Trial announcement finished (%d users)", len(ids))
    with db() as con:
        ids = [r["user_id"] for r in con.execute("SELECT user_id FROM users WHERE banned=0 AND cmd_notified=0")]
    for uid in ids:
        await send_trigger_intro(uid)
        await asyncio.sleep(0.06)
    logging.info("Custom command announcement finished (%d users)", len(ids))


# ---- background housekeeping ----------------------------------------------------------------
async def run_due_jobs():
    with db() as con:
        rows = con.execute("SELECT * FROM scheduled WHERE run_at<=? ORDER BY run_at LIMIT 20", (time.time(),)).fetchall()
        if rows:
            marks = ",".join("?" * len(rows))
            con.execute(f"DELETE FROM scheduled WHERE id IN ({marks})", [r["id"] for r in rows])
    for r in rows:
        uid, cid = r["owner_id"], r["connection_id"]
        if conn_owner(cid) is None:
            continue
        try:
            if r["kind"] == "send":
                await _send_as_owner(cid, r["chat_id"], r["text"])
                log_activity(uid, "scheduled sent", r["chat_id"], r["label"], (r["text"] or "")[:60])
            else:
                await _delete_biz(cid, [r["message_id"]])
        except Exception:
            logging.exception("scheduled job failed")
            if r["kind"] == "send":
                with use_lang(uid):
                    await notice(uid, f"{E('err')} {sc('A scheduled message could not be sent.')}", ttl=15)


async def expire_shh():
    t = time.time()
    for key, cfg in list(_SHH.items()):
        if cfg["until"] and cfg["until"] <= t:
            shh_drop(key)
            owner = conn_owner(key[0])
            if owner:
                with use_lang(owner):
                    await notice(owner, f"{E('lock')} {sc('Shh ended')} ➛ {chat_ref(key[1], cfg['label'])}", ttl=12)
                log_activity(owner, "shh off", key[1], cfg["label"], "timer")


def purge_old():
    cutoff = (now() - timedelta(hours=DELETED_CACHE_HOURS)).isoformat()
    with db() as con:
        con.execute("DELETE FROM msg_cache WHERE sent_at<?", (cutoff,))
        con.execute("DELETE FROM activity WHERE ts<?", (time.time() - 30 * 86400,))
        con.execute("DELETE FROM call_log WHERE ts<?", (time.time() - 30 * 86400,))


async def housekeeping_loop():
    tick = 0
    while True:
        try:
            await run_due_jobs()
            await expire_shh()
            if tick % 300 == 0:
                purge_old()
        except Exception:
            logging.exception("housekeeping failed")
        tick += 1
        await asyncio.sleep(1)


def clean_temp_media():
    """Files are only temporary unless SAVE_MEDIA_LOCALLY is on: remove anything left over."""
    if TEMP_DIR.exists():
        for f in TEMP_DIR.iterdir():
            f.unlink(missing_ok=True)


@dp.business_message()
async def archive_reply(message: Message):
    # 1. Must be a reply to a media message, inside a business connection.
    if not message.text or not message.business_connection_id or not message.from_user:
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

    # 4. The text must be the default word or one of the owner's custom commands.
    if message.text.strip().casefold() not in trigger_words(owner_id):
        return

    # 5. Is there media in the replied message?
    media, media_type, extension = extract_media(original)
    if media is None:
        await notice(owner_id, f"{E('err')} {sc('No supported media found in the replied message.')}", ttl=10)
        return

    # 6. Never process the same trigger twice.
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

    # 7. Per-user daily limit (each owner has their own counter).
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

    # 8. Download, deliver to the owner, then count it.
    if SAVE_MEDIA_LOCALLY:
        user_dir = SAVE_DIR / str(owner_id)
        user_dir.mkdir(parents=True, exist_ok=True)
        filepath = user_dir / f"{message.chat.id}_{original.message_id}_{media_type}{extension}"
    else:  # temporary file: removed again as soon as it has been delivered and backed up
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        filepath = TEMP_DIR / f"{owner_id}_{message.chat.id}_{original.message_id}_{uuid.uuid4().hex[:8]}{extension}"

    try:
        await bot.download(media.file_id, destination=filepath)
        sent = await deliver(media_type, owner_id, FSInputFile(filepath))
        await store_media_copy(sent, owner_id, message.chat, media_type)

        with db() as con:
            con.execute(
                "INSERT INTO archives(user_id, source_chat_id, source_message_id, media_type, file_path, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (owner_id, message.chat.id, original.message_id, media_type,
                 str(filepath) if SAVE_MEDIA_LOCALLY else "", now().isoformat()))
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
    finally:
        if not SAVE_MEDIA_LOCALLY:
            filepath.unlink(missing_ok=True)


# ----------------------------------------------------------------------------
# Backup: the database is sent to a Telegram chat and PINNED there. After a host crash
# the bot finds the pinned file on startup and restores it automatically.
# Best practice: create a private channel, add the bot as admin (post + pin rights) and
# put its ID in .env as BACKUP_CHAT_ID=-100xxxxxxxxxx. Default is your own chat with the bot.
# Telegram limit: bots can download files up to 20 MB (the DB is far smaller).
# ----------------------------------------------------------------------------
BACKUP_CHAT_ID = int(os.getenv("BACKUP_CHAT_ID", "0")) or OWNER_ID
BACKUP_EVERY_HOURS = float(os.getenv("BACKUP_EVERY_HOURS", "3"))
_backup_lock = asyncio.Lock()
_last_backup_hash = None


def _snapshot_db():
    """Consistent copy of the live database (safe while the bot is running), gzipped."""
    import gzip
    tmp = DB_PATH.with_suffix(".snapshot")
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    data = gzip.compress(tmp.read_bytes(), compresslevel=9)
    tmp.unlink(missing_ok=True)
    return data


async def backup_now(reason="auto", force=False):
    """Send a snapshot to BACKUP_CHAT_ID and pin it. Skips if nothing changed."""
    global _last_backup_hash
    import hashlib
    async with _backup_lock:
        try:
            if not DB_PATH.exists():
                return False
            data = await asyncio.to_thread(_snapshot_db)
            digest = hashlib.sha256(data).hexdigest()
            if digest == _last_backup_hash and not force:
                return True
            name = f"backup_{now():%Y%m%d_%H%M%S}.sqlite3.gz"
            msg = await bot.send_document(
                BACKUP_CHAT_ID, BufferedInputFile(data, filename=name),
                caption=f"DB backup ({reason}) {now():%Y-%m-%d %H:%M} UTC",
                parse_mode=None, disable_notification=True)
            try:
                await bot.pin_chat_message(BACKUP_CHAT_ID, msg.message_id, disable_notification=True)
            except Exception:
                logging.exception("Could not pin backup (give the bot pin rights)")
            _last_backup_hash = digest
            logging.info("Backup sent (%s, %d bytes)", reason, len(data))
            return True
        except Exception:
            logging.exception("Backup failed")
            return False


async def restore_if_missing():
    """Startup: if there is no database (fresh host), pull the pinned backup."""
    import gzip
    if DB_PATH.exists() and DB_PATH.stat().st_size > 0:
        return
    try:
        chat = await bot.get_chat(BACKUP_CHAT_ID)
        pinned = chat.pinned_message
        if not pinned or not pinned.document:
            logging.warning("No pinned backup found, starting with an empty database")
            return
        buf = BytesIO()
        await bot.download(pinned.document.file_id, destination=buf)
        DB_PATH.write_bytes(gzip.decompress(buf.getvalue()))
        logging.info("DATABASE RESTORED from pinned backup")
        print("Database restored from Telegram backup.")
    except Exception:
        logging.exception("Restore failed")


async def backup_loop():
    await asyncio.sleep(60)
    while True:
        await backup_now("auto")
        await asyncio.sleep(max(0.1, BACKUP_EVERY_HOURS) * 3600)


@dp.message(Command("backup"))
async def backup_command(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    await tidy(message)
    ok = await backup_now("manual", force=True)
    await notice(message.chat.id, f"{E('ok')} {sc('Backup sent')}" if ok
                 else f"{E('err')} {sc('Backup failed, check bot.log')}", ttl=8)


@dp.message(Command("restore"))
async def restore_command(message: Message):
    """Reply to a backup file (.sqlite3.gz) with /restore to load it (owner only)."""
    import gzip
    if message.from_user.id != OWNER_ID:
        return
    await tidy(message)
    doc = message.reply_to_message.document if message.reply_to_message else None
    if not doc:
        return await notice(message.chat.id, "Reply to a backup file with <code>/restore</code>", ttl=8)
    buf = BytesIO()
    await bot.download(doc.file_id, destination=buf)
    raw = buf.getvalue()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass  # plain .sqlite3 file
    if not raw.startswith(b"SQLite format 3"):
        return await notice(message.chat.id, f"{E('err')} {sc('Not a valid database file.')}", ttl=8)
    await backup_now("before-restore", force=True)
    tmp = DB_PATH.with_suffix(".restore")
    tmp.write_bytes(raw)
    src = sqlite3.connect(tmp)
    dst = sqlite3.connect(DB_PATH)
    try:
        src.backup(dst)   # overwrites the live DB in place
    finally:
        src.close()
        dst.close()
        tmp.unlink(missing_ok=True)
    init_db()
    await notice(message.chat.id, f"{E('ok')} {sc('Database restored.')}", ttl=10)


async def main():
    await restore_if_missing()
    keep_alive()
    init_db()
    load_runtime_state()
    load_emoji_ids()
    clean_temp_media()
    _TASKS.add(asyncio.create_task(backup_loop()))
    _TASKS.add(asyncio.create_task(housekeeping_loop()))
    if ANNOUNCE_TRIAL_ON_START:
        _TASKS.add(asyncio.create_task(announce_trial_loop()))
    if not HAS_ICON:
        print("Note: aiogram < 3.25 -> button icons/colours disabled (pip install -U aiogram)")
    print("Media Archive Bot Started Successfully!")
    print("Bot is running...")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "business_connection", "business_message", "edited_business_message",
                         "deleted_business_messages", "callback_query"]
    )


if __name__ == "__main__":
    asyncio.run(main())
