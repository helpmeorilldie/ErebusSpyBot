<div align="center">

# ✨ Erebus · Media Archive Bot 👾

**Save any photo, video or file from your chats with one word, catch deleted messages, and automate your Telegram account.**
Telegram Business bot · Archive · Deleted-message saver · Repeater · Auto-reply · Scheduler · Premium · Admin panel

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![aiogram](https://img.shields.io/badge/aiogram-3.x-26A5E4?logo=telegram&logoColor=white)
![SQLite](https://img.shields.io/badge/Database-SQLite-003B57?logo=sqlite&logoColor=white)
![Languages](https://img.shields.io/badge/Languages-English%20%7C%20%E0%A4%B9%E0%A4%BF%E0%A4%A8%E0%A5%8D%E0%A4%A6%E0%A5%80-8A2BE2)
![Status](https://img.shields.io/badge/Status-Active-success)

*By Haze · [@Noxmirage](https://t.me/Noxmirage) · Channel: [@linksfromerebus](https://t.me/linksfromerebus)*

[▶️ Watch the 1-minute tutorial](https://youtube.com/shorts/KRKAUY3g7wk?si=G5eeDrXGJ5ZGfX5v)

</div>

---

## 📑 Table of contents

1. [What it does](#-what-it-does)
2. [Quick start](#-quick-start)
3. [Configuration](#%EF%B8%8F-configuration)
4. [How users use it](#-how-users-use-it)
5. [Commands you type in your chats](#-commands-you-type-in-your-chats)
6. [Deleted-message saver & free trial](#-deleted-message-saver--free-trial)
7. [Plans, limits & payments](#-plans-limits--payments)
8. [Admin panel & commands](#-admin-panel--commands)
9. [Storage, privacy & backups](#-storage-privacy--backups)
10. [Project files](#-project-files)
11. [Troubleshooting](#-troubleshooting)
12. [Security notes](#-security-notes)

---

## 🌟 What it does

| Area | What you get |
|---|---|
| 📸 **Archive** | Reply to any media with `shit` in any chat and the copy lands in your bot chat. Photos, videos, GIFs, files, video notes |
| 🗑 **Deleted & edited messages** | When someone deletes **or edits** a message in your private chats, the bot sends it to you (old and new text). Premium feature with a **3-day free trial** for everyone |
| ⚡ **Repeater** | Repeat messages, photos, stickers or voice notes. Presets, random delay, pause / resume, live progress |
| 🔇 **Shh mode** | Instantly deletes what the *other person* sends in one chat. Timers, allow-lists, optional log |
| 💬 **Auto reply** | Away mode with schedule, per-chat messages and keyword replies |
| ⏰ **Scheduler** | `.later` sends at a time you choose, `.del` makes messages delete themselves |
| 🧩 **Snippets & translate** | Type `.price` to expand saved text, `.tr hi hello` to translate |
| 🛡 **Chat rules** | Block words, link filter, anti-flood, and a one-tap `.panic` |
| 📊 **Limits** | Daily free limit, unlimited premium, per-user custom limits, resets at 00:00 UTC |
| 💳 **Payments** | UPI QR generated in the bot, screenshot goes to the owner, one-tap approve |
| 🛠 **Admin panel** | Statistics, **revenue**, paged **all-users list**, user info by `@username` or ID, give / remove premium, ban, broadcast |
| 💾 **Backups** | Database auto-backed up to Telegram and restored automatically after a host crash |
| 🌐 **Languages** | English, हिन्दी and Hinglish menus |
| 📨 **Contact admin** | Users message the admin through the bot, the admin replies with one tap, and the user can reply back |
| 🎨 **UI** | Small-caps font, animated custom emojis (plain fallback), self-cleaning chat |

---

## 🚀 Quick start

### 1. Requirements

- Python **3.10 or newer**
- A bot token from [@BotFather](https://t.me/BotFather) (enable **Business Mode** for the bot there)
- A Telegram account with **Telegram Premium** (Business chatbots are a Premium feature of the account that connects the bot)
- (Recommended) a private channel for backups, with the bot added as **admin**

### 2. Install

```bash
pip install -U aiogram python-dotenv aiohttp "qrcode[pil]"
```

> `aiogram` 3.25 or newer gives you coloured buttons with icons. Older 3.x versions still work, the icons are simply skipped.

### 3. Create a `.env` file next to the bot

```env
BOT_TOKEN=123456:your-token-here
OWNER_ID=123456789
CHANNEL=@yourchannel
GROUP=@yourgroup
BACKUP_CHAT_ID=-1001234567890
MEDIA_CHAT_ID=-1001234567890
```

### 4. Run

```bash
python botV6.py
```

You should see `Media Archive Bot Started Successfully!`. Open the bot and send `/start`.

### 5. Connect your account

1. In Telegram open **Settings → Business → Chatbots**.
2. Add your bot.
3. Give it the permissions you need: **reply to messages** (archive, repeater, auto-reply) and **manage / delete messages** (`.shh`, `.del`, rules, command cleanup).

> ⚠️ **Run only ONE copy of the bot per token.** Two copies split the updates and buttons become unreliable.

---

## ⚙️ Configuration

Settings are read from `.env` (or environment variables). The options marked ✏️ can also be edited at the top of `botV6.py`.

### Required

| Setting | Meaning |
|---|---|
| `BOT_TOKEN` | Your bot token from @BotFather |
| `OWNER_ID` | Your numeric Telegram ID. Main admin, receives payment screenshots |

### Optional

| Setting | Default | Meaning |
|---|---|---|
| `CHANNEL` / `GROUP` | in file | Users must join both before using the bot |
| `FREE_DAILY_LIMIT` | `20` | Free archives per day |
| `UPI_ID` / `UPI_NAME` | in file | UPI address and name on the payment QR |
| `PLANS` ✏️ | `1 / 2 / 3 months` | `{months: (days, price)}` → ₹29, ₹39, ₹49 |
| `DB_PATH` | `bot_data.sqlite3` | Database file |
| `SAVE_MEDIA_LOCALLY` ✏️ | `0` (off) | `1` keeps every archived file in `./saved_media`. Off means files are **temporary** and deleted after delivery and backup |
| `TG_MEDIA_BACKUP` ✏️ | `1` (on) | Send a copy of every archived file to `MEDIA_CHAT_ID` |
| `MEDIA_CHAT_ID` | falls back to `BACKUP_CHAT_ID`, then your chat | Private channel (`-100…`) that stores media copies |
| `BACKUP_CHAT_ID` | your chat | Where the database backup is posted and pinned |
| `BACKUP_EVERY_HOURS` | `3` | Hours between automatic database backups |
| `TRIAL_DAYS` ✏️ | `3` | Free trial of the deleted-message saver |
| `DELETED_CACHE_HOURS` | `72` | How long copies of incoming messages are kept for the saver |
| `ANNOUNCE_TRIAL_ON_START` | `1` | Tell existing users about the trial once after an update |
| `USE_CUSTOM_EMOJI` | `1` | Set `0` if animated emojis don't show |
| `EMOJI_FILE` | `emoji_ids.json` | Animated-emoji overrides (set with `/emojis`) |

---

## 👤 How users use it

1. Send `/start`, join the channel and group, tap **I've Joined**.
2. Connect the bot under **Settings → Business → Chatbots**.
3. In any chat, **reply to a photo / video / file with `shit`**. The copy arrives in your bot chat.
4. Only the account owner can trigger it. The `shit` message itself stays in your chat history as you wrote it.

**Main menu**

| Button | What it does |
|---|---|
| How To Archive | Steps and the video tutorial |
| My Premium / My Statistics | Plan, expiry, today's usage, total archived |
| Membership | Re-check channel and group membership |
| Buy Premium | Plans and UPI payment |
| Repeated Message | Configure the repeater |
| Deleted Messages | Deleted-message saver status, on / off |
| Auto Reply | Away mode, schedule, timezone |
| Tools | Keyword replies, snippets, scheduled messages, activity log, commands, panic |
| Language | English, हिन्दी or Hinglish |
| Contact Admin | Send a message (text, photo, voice, file) to the admin and get the reply in the bot |

---

## ⌨️ Commands you type in your chats

These work in **your own chats** through the Business connection. Only you can use them, and the bot removes the command message again. Everything is also configurable from the bot menus.

### ⚡ Repeater

| Command | What it does |
|---|---|
| `.Axclerate` | Start with your saved settings |
| `.Axclerate 20 0.5 hi \| hello` | 20 messages, every 0.5 s, cycling the texts |
| `.Axclerate 20 0.5-2` | Random delay between 0.5 and 2 seconds |
| `.Axclerate morning` | Start a saved **preset** |
| `.Axpause` · `.Axresume` · `.Axstop` | Control the running repeater (buttons on the live status message work too) |

Minimum interval is **0.1 s**. The cycle can contain text, photos, stickers, voice notes, videos and files (add them from **Repeated Message → Add Media**).

> ⚠️ Fast repeating can get your Telegram account limited or banned. The bot warns about this and does **not** add restrictions: use at your own risk.

### 🔇 Shh mode

| Command | What it does |
|---|---|
| `.shh` | Toggle for **this chat only**. The other person's messages are deleted instantly, yours never are |
| `.shh 30m` | Turn on for 30 minutes (`s`, `m`, `h`, `d`) |
| `.shh allow urgent \| help` | Keep messages containing these words |
| `.shh allowmedia photo voice` | Keep these media types |
| `.shh log` | Save a copy of what is deleted (premium / trial) |
| `.shh off` · `.mute` | Turn off · alias of `.shh` |

### 💬 Auto reply, scheduling and more

| Command | What it does |
|---|---|
| `.afk 2h in a meeting` · `.back` | Away reply on / off (each chat gets it once every 6 hours) |
| `.afkhere message` | Different away message for just this chat |
| `.later 30m text` · `.later 21:30 text` | Send later (clock times use your timezone) |
| `.del 10s text` | Send text that deletes itself. Or reply to your own message with `.del 10s` |
| `.save` | Reply to any message to save it into the bot |
| `.tr hi how are you` | Turn your message into Hindi (any language code). Reply with `.tr en` to read a message privately |
| `.kw add price \| It costs ₹99` | Keyword auto-reply (`.kw del`, `.kw list`) |
| `.snip add price \| It costs ₹99` | Snippet, then type `.price` to expand it |
| `.block spam \| scam` · `.block off` | Delete messages from this person containing these words |
| `.nolinks` · `.antispam` | Toggle link filter / flood filter for this chat |
| `.panic` (or `/panic`) | Stop all repeaters, shh, scheduled sends and away mode |

---

## 🗑 Deleted & edited message saver, and free trial

- Saves what the **other person** sent in your **private chats** (text, photos, videos, voice notes, stickers, files) and sends it to you in the bot the moment they delete it.
- If they **edit** a message, you get the old and the new version.
- **Bot commands are never saved** (`.shh`, `.later`, snippets, `/start`...), so if the other person runs this bot too, your commands don't show up as "deleted" for them.
- Needs the bot connected under **Settings → Business → Chatbots**.
- **Premium feature**, but **every user gets a 3-day free trial**. New users start the trial when they join, existing users when the update goes live. Everyone is told about it once.
- Copies are kept for `DELETED_CACHE_HOURS` (72 by default), then removed. Turn the saver off any time from **Deleted Messages**.
- Messages the bot deletes itself (`.shh`, block rules) are never saved.

---

## 💳 Plans, limits & payments

| Plan | Archives / day | Validity | Price |
|---|---|---|---|
| Free | 20 | – | Free |
| Premium 1 month | Unlimited | 30 days | ₹29 |
| Premium 2 months | Unlimited | 60 days | ₹39 |
| Premium 3 months | Unlimited | 90 days | ₹49 |

Daily counters reset at **00:00 UTC**. Premium also unlocks the deleted-message saver after the trial ends.

**Payment flow**

1. User opens **Buy Premium** and picks a plan.
2. The bot generates a **UPI QR** with the amount filled in.
3. User pays, taps **I've Paid** and sends a screenshot.
4. The screenshot reaches the owner with **Approve / Reject** buttons.
5. On approval premium activates instantly and the user is notified. Buying again extends it.

---

## 🛠 Admin panel & commands

Open it with the **Admin Panel** button on the main menu (owner only).

| Button | Purpose |
|---|---|
| Statistics | Users, active, premium, **free-trial users**, banned, joined today, archives today, pending payments, revenue |
| **Revenue** | All time, today, 7 / 30 days, this month, by month and by plan. Counts only plans **users bought** and you approved, never premium you gave yourself |
| **All Users** | Every user, paged, with a **Download List** CSV button. Names link to profiles |
| Premium Users | Active premium members |
| User Info | Details of any user by `@username` **or numeric ID** |
| Give / Remove Premium | Extend or remove premium with a few taps |
| Ban User / Unban User | Block or unblock |
| Broadcast / Message One User | Message everyone or one person |
| Reply (on a user message) | Users write to you with **Contact Admin**. Tap **Reply** under their message, type your answer, and it is delivered to them with a Reply button for the next turn |

| Command | What it does |
|---|---|
| `/userinfo @user` or `/userinfo 123456789` | Full details of a user |
| `/premium @user days` · `/removepremium @user` | Grant / remove premium |
| `/setlimit @user number\|default` | Custom daily limit |
| `/ban @user` · `/unban @user` | Block or unblock |
| `/msg @user text` | Private message |
| `/emojis` · `/emojireset name\|all` | Animated emoji setup |
| `/backup` · `/restore` | Back up now · reply to a backup file to restore |

Every user command accepts an `@username` **or** a numeric ID. A person must have started the bot once to be found.

---

## 🔐 Storage, privacy & backups

**Archived media**

- By default files are **temporary**: downloaded, handed to the owner, copied to the Telegram backup channel, then **deleted from the server**.
- Set `SAVE_MEDIA_LOCALLY=1` to also keep them in `./saved_media`.
- The backup-channel caption names the owner and chat by **@username** (or by profile name as a tap-to-open link when there is no username), followed by their numeric ID.
- The privacy text shown in **How To Archive** always matches your settings: if you turn off both `TG_MEDIA_BACKUP` and `SAVE_MEDIA_LOCALLY` it says *"No backup is saved, for your privacy. You can only view your saved photos here in the bot."*

**Database backups**

- The database is snapshotted every `BACKUP_EVERY_HOURS`, sent to `BACKUP_CHAT_ID` and **pinned**. It is also backed up right after every approved payment.
- If the server resets, the bot finds the pinned backup on startup and **restores it automatically**.
- `/backup` backs up now. Reply to a backup file with `/restore` to load it (a safety copy is taken first).
- Older databases are upgraded automatically when the new version starts.

---

## 📂 Project files

```
botV6.py                 the whole bot
README.md                this file
.env                     your settings (never share it)
bot_data.sqlite3         database (created automatically)
emoji_ids.json           optional animated-emoji overrides
saved_media/             only used when SAVE_MEDIA_LOCALLY=1
bot.log                  log file
```

---

## 🩺 Troubleshooting

| Problem | Fix |
|---|---|
| Nothing happens when I reply `shit` | Check **Settings → Business → Chatbots**: the bot must be added and allowed to reply. Make sure you started the bot at least once |
| `.shh`, `.del` or commands don't delete messages | Give the bot the **manage / delete messages** permission in the Business chatbot settings |
| Deleted messages aren't arriving | The trial may have ended (premium needed), the saver may be off in **Deleted Messages**, or the bot isn't connected |
| Messages flash before `.shh` deletes them | Telegram delivers a message to you before the bot can delete it. The bot deletes it as fast as it can |
| Buttons are slow or do nothing | Make sure only **one** copy of the bot is running |
| Animated emojis or coloured buttons don't show | Set `USE_CUSTOM_EMOJI=0`, or `pip install -U aiogram` |
| Payment QR missing | `pip install "qrcode[pil]"`. Users still see the UPI ID |
| No backups arrive | Set `BACKUP_CHAT_ID` to your channel ID (starts with `-100`) and make the bot an **admin** there |
| `.tr` says translation failed | It uses Google Translate's unofficial web endpoint, which can be blocked from some hosts. Try again later |
| "User not found" | The user must have started the bot once. Try their numeric ID |

---

## 🔒 Security notes

- **Never share your bot token or `.env`.** If a token was ever posted publicly, revoke it in @BotFather (`/revoke`).
- Only `OWNER_ID` can use admin features, and every admin button and command is re-checked on each press.
- Keep your backup and media channels **private**: they contain user data.
- The deleted-message saver stores other people's messages for a short time. Use it responsibly and respect the privacy and laws that apply to you and the people you chat with.
- Spam tools like the repeater can get your Telegram account restricted. You are responsible for how you use them.

---

<div align="center">

Made with 💜 by **Haze** · [@Noxmirage](https://t.me/Noxmirage) · [Channel](https://t.me/linksfromerebus)

</div>
