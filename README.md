# 🎂 Discord Birthday Bot (Multi-Server, MySQL)

A Discord bot that automatically detects birthdays when someone simply types
a date like `02.08.2000` in the chat, and announces them every year. Runs as
**a single bot process across any number of servers at once** - all data
(birthdays and channel configuration) is stored strictly separated per server
in a **MySQL database**.

## Features

- **Automatic detection:** Type a date in the input channel (or in any
  channel, if none is configured) - with a year (`02.08.2000`) or without
  (`02.08.` / `02.08`, for anyone who'd rather not reveal their birth year).
- **Strict per-server separation:** Birthdays, input channel, and
  announcement channel are all stored separately per server (guild ID).
  Birthdays entered on Server A never show up on Server B.
- **Yearly reminder:** Once a day (default: 9 AM) the bot checks **every**
  configured server individually and posts an embed with a mention of the
  person in question.
- **`/birthdays [count]`** – shows the next upcoming birthdays on this server.
- **`/birthday [person]`** – shows a person's birthday on this server.
- **`/deletebirthday`** – deletes your own saved birthday.
- **`/birthdaystatus`** – shows the current channel configuration for this server.
- **`/setinputchannel [channel]`** – sets the channel where dates are captured
  (admin, requires "Manage Server").
- **`/setbirthdaychannel [channel]`** – sets the channel for birthday
  announcements (admin, requires "Manage Server").

## Why MySQL instead of a JSON file?

Running on multiple servers means multiple independent, potentially
concurrently-changing datasets (birthdays *and* channel configuration per
server). A database avoids write conflicts, allows clean queries ("all
birthdays on server X that are due today"), and is easy to extend. Tables are
created automatically on first start.

## Setup

### 1. Create the bot in the Discord Developer Portal

1. Go to https://discord.com/developers/applications -> "New Application"
2. Click **Bot** on the left -> **Add Bot**
3. Under **Privileged Gateway Intents**, enable:
   - ✅ **Message Content Intent**
   - ✅ **Server Members Intent**
4. Copy the **token**

### 2. Invite the bot

1. **OAuth2 -> URL Generator**
2. Scopes: `bot` **and** `applications.commands`
3. Bot permissions: `Send Messages`, `Read Message History`, `Add Reactions`, `Embed Links`
4. Use the generated invite link on every server you want - **the same bot,
   the same process, the same `.env`** works for all servers simultaneously.

### 3. Create the MySQL database

If your host already has a MySQL instance (e.g. via a control panel), create
a new database and user there. Via the command line:

```sql
CREATE DATABASE birthdaybot CHARACTER SET utf8mb4;
CREATE USER 'birthdaybot'@'localhost' IDENTIFIED BY 'your_db_password';
GRANT ALL PRIVILEGES ON birthdaybot.* TO 'birthdaybot'@'localhost';
FLUSH PRIVILEGES;
```

The tables themselves (`birthdays`, `guild_config`) are created
**automatically** by the bot on first start - nothing else needed here.

### 4. Install the project

```bash
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`: `DISCORD_TOKEN` plus the `DB_*` variables matching the
database you created in step 3.

### 5. Run the bot

```bash
python bot.py
```

```
✅ Logged in as YourBotName (ID: ...)
🔄 6 slash commands synced.
```

## Per-server setup

On **every** server the bot joins, run once:

```
/setinputchannel #birthday-log
/setbirthdaychannel #announcements
```

After that, everything works independently for that server - other servers
running the same bot are completely unaffected.

## Database schema

```sql
birthdays
├── guild_id  (server ID)
├── user_id   (Discord user ID)
├── day
├── month
└── year      (NULL if no year was given)
   PRIMARY KEY (guild_id, user_id)

guild_config
├── guild_id             (server ID, PRIMARY KEY)
├── birthday_channel_id  (channel for announcements)
└── input_channel_id     (channel for capturing typed dates)
```

Since `user_id` is **not** the sole key but always paired with `guild_id`,
the same person can have different (or no) birthday data on different
servers.

## Troubleshooting: slash commands not showing up / showing up twice

- **Not showing up at all:** Global command sync can take up to ~1 hour to
  propagate to Discord clients, and clients cache the command list. Try a
  hard restart of the Discord client (fully quit and reopen, not just
  Ctrl+R). For instant testing, set `GUILD_ID` in `.env` temporarily.
- **Showing up twice / an old command lingering after removal:** This
  happens if `GUILD_ID` was set at some point in the past - that creates a
  guild-specific command list which overrides the global one for that server
  and doesn't update automatically afterwards. Run:
  ```bash
  python3 clear_guild_commands.py YOUR_SERVER_ID
  ```
  then make sure `GUILD_ID` is empty in `.env` going forward.
