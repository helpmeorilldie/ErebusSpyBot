TELEGRAM MEDIA ARCHIVE BOT

1. Install Python 3.10+.
2. In this folder run: pip install -r requirements.txt
3. Copy .env.example to .env and fill BOT_TOKEN and OWNER_ID.
4. Make the bot an administrator in @linksfromerebus and @ecochamber
   so it can check membership.
5. Enable Telegram Business connection for each user who wants to archive.
   Each user must /start the bot first.
6. Run: python bot.py

Admin commands:
  /premium @username 30
  /removepremium @username
  /userinfo @username
  /ban @username
  /unban @username

IMPORTANT:
- Username-based admin commands work only for people who have already
  started the bot and whose username is recorded. Telegram does not let
  bots resolve arbitrary usernames to numeric user IDs.
- Free users get 20 successful archives per UTC calendar day.
  Premium users have no daily archive cap.
- SQLite stores users, premium expiry, connections, archive records,
  deduplication keys, and broadcast history.
- Keep .env and bot_data.sqlite3 private. Back them up securely.
- This starter implementation broadcasts text messages only.
- Telegram controls inline keyboard button styling; bots cannot force
  a blue button color.
- Use archiving only for media you are authorized to access and retain.
