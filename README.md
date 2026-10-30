# 📸 Erebus Media Archive Bot

A Telegram bot that works through **Telegram Business**. Reply to any photo, video, GIF, document or video note in your chats with a trigger word, and the bot sends a copy to your private chat with it. It has per-user daily limits, a premium system, and a built-in **UPI payment flow** with custom QR codes and admin approval.

> Built with [aiogram 3](https://docs.aiogram.dev/) and SQLite.

---

## ✨ Features

- **Business archiving**: reply to media with the trigger word (default `shit`) from your connected Business account.
- **Owner-only trigger**: only the owner of a Business connection can trigger it, and media is only ever sent to that owner. This also prevents duplicate copies when two connected accounts chat with each other.
- **Per-user daily limits**: every user has their own counter (default 20/day). Premium is unlimited, and admins can set a custom limit for one user.
- **Premium + payments**
  - Plans: 1 month ₹29, 2 months ₹39, 3 months ₹49 (editable)
  - Custom UPI QR per plan, with the amount pre-filled
  - User sends a payment screenshot, and the admin gets it in the bot with ✅ Approve / ❌ Reject buttons
  - Users are notified automatically when premium is activated
- **Admin panel**: statistics, user list, premium list, broadcast to everyone, message a single user, ban/unban.
- **Force-join**: users must join your channel and group before using the bot.
- **Organised SQLite database** with automatic migrations.

---

## 📋 Requirements

- Python **3.10+**
- A bot token from [@BotFather](https://t.me/BotFather) with **Business Mode** enabled (BotFather → your bot → Bot Settings → Business Mode)
- A Telegram account with **Telegram Premium** to connect the bot as a chatbot (Settings → Business → Chatbots)
- The bot added as an **administrator** in your channel and group (needed to check membership)

---

## 🚀 Setup

```bash
git clone https://github.com/<your-username>/<your-repo>.git
cd <your-repo>

python -m venv venv
# Windows:  venv\Scripts\activate
# Linux/Mac: source venv/bin/activate

pip install -r requirements.txt

cp .env.example .env      # then edit .env with your values
python bot.py
```

### Configuration (`.env`)

| Variable | Required | Default | Description |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | n/a | Token from @BotFather |
| `OWNER_ID` | ✅ | n/a | Your numeric Telegram user ID (admin) |
| `CHANNEL` | | `@linksfromerebus` | Channel users must join |
| `GROUP` | | `@ecochamber` | Group users must join |
| `DB_PATH` | | `bot_data.sqlite3` | SQLite database file |
| `FREE_DAILY_LIMIT` | | `20` | Daily archives for free users |
| `UPI_ID` | | `Ahmadammar@fam` | UPI ID that receives payments |
| `UPI_NAME` | | `Erebus Premium` | Payee name shown in the UPI app |
| `USE_CUSTOM_EMOJI` | | `1` | Set `0` to switch custom emojis off |

Plans and prices are set in the `PLANS` line near the top of `bot.py`:

```python
PLANS = {1: (30, 29), 2: (60, 39), 3: (90, 49)}   # months: (days, price in INR)
```

---

## 🎨 Interface

- Clean, panel-style screens: quote header, emoji-led rows and wide buttons, in the bot's small-caps font.
- **Self-cleaning chat**: menus edit in place, and prompts, QR pictures, typed commands and short notices are deleted once they are no longer needed.
- **Animated (custom) emojis & coloured buttons** (optional): send `/emojis` to see the emoji names, then send the bot a message like `ok` + any custom emoji to set it. IDs are saved in `emoji_ids.json`. This needs Telegram Premium on the bot owner's account. If anything breaks, set `USE_CUSTOM_EMOJI=0` in `.env`. Button icons and colours need `aiogram>=3.25`.

---

## 🧭 How it works

### For users
1. Start the bot and join the channel and group.
2. Connect the bot in **Telegram Settings → Business → Chatbots**.
3. In any chat of your Business account, **reply to a media message** with the trigger word.
4. The bot sends the copy to your chat with the bot.

### Buying premium
1. **💳 Buy Premium** → choose a plan.
2. Scan the QR with any UPI app and pay the exact amount.
3. Tap **✅ I've Paid** and send the screenshot.
4. Once the admin approves, premium is activated and you get a message.

---

## 🛠 Admin commands

Everything is also reachable from the **🛠 Admin Panel** button in `/start`.

| Command | What it does |
|---|---|
| `/premium @user days` | Give premium for N days (the user is notified) |
| `/removepremium @user` | Remove premium |
| `/setlimit @user number` or `default` | Custom daily limit for one user |
| `/userinfo @user` | Plan, usage, total archives, connections |
| `/msg @user text` | Send a message to one user (an ID works too) |
| `/ban @user` / `/unban @user` | Block or unblock a user |

Users can only be found by username after they have started the bot.

---

## 🗄 Database

SQLite file, created and migrated automatically on start.

| Table | Purpose |
|---|---|
| `users` | Everyone who started the bot (plus `custom_limit`, `banned`) |
| `premium` | Premium expiry per user |
| `business_connections` | Connected Business accounts and their owners |
| `usage_daily` | Per-user, per-day counter that the limit checks |
| `archives` | History of every archived file |
| `payments` | Payment requests: pending / approved / rejected |
| `processed_messages` | Stops the same trigger being handled twice |
| `broadcasts` | Broadcast history |

All timestamps are UTC. The daily limit resets at 00:00 UTC.

---

## 📁 Project structure

```
.
├── bot.py             # the whole bot
├── requirements.txt
├── .env.example
└── README.md
```

Created at runtime (keep them out of Git): `.env`, `emoji_ids.json`, `bot_data.sqlite3`, `saved_media/`, `bot.log`.

---

## 🔒 Security notes

- **Never commit `.env`.** It holds your bot token.
- Payments are approved **manually**, so always check the screenshot against your UPI app before tapping Approve.
- Only `OWNER_ID` can use admin buttons and commands.

## ⚠️ Responsible use

This bot copies media from chats that you own through your own Business account. Only archive content you have the right to keep, and follow Telegram's Terms of Service and the privacy laws that apply to you.

## 👨‍💻 Credits

- Developer: [@Noxmirage](https://t.me/Noxmirage)
- Channel: [@HazenNexus](https://t.me/HazenNexus)
- Updates: [@linksfromerebus](https://t.me/linksfromerebus)
