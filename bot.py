import os
import re
import asyncio
from datetime import datetime, date, time as dtime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

import db

# ---------------------------------------------------------------------------
# Configuration (all via the .env file)
# ---------------------------------------------------------------------------

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
ANNOUNCE_HOUR = int(os.getenv("ANNOUNCE_HOUR", "9"))
ANNOUNCE_MINUTE = int(os.getenv("ANNOUNCE_MINUTE", "0"))
GUILD_ID = os.getenv("GUILD_ID")  # optional: only useful for instant sync while testing on one server

# Matches dates like 2.8.2000, 02.08.2000, 2.8.00 -> with year
# as well as 2.8. / 02.08. / 2.8 / 02.08 -> without year (trailing dot is optional)
DATE_PATTERN = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.?(\d{2,4})?\s*$")

EMBED_COLOR = 0xFFC0CB

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True  # required to read message content (date entries)
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


def parse_year(year_str: str) -> int:
    """Also allows two-digit years (00 -> 2000, 99 -> 1999)."""
    year = int(year_str)
    if len(year_str) == 2:
        year += 2000 if year <= 30 else 1900
    return year


def next_occurrence(day: int, month: int) -> date:
    """Returns the next date this day/month occurs on (today counts as 'in 0 days')."""
    today = date.today()
    try:
        this_year = date(today.year, month, day)
    except ValueError:
        this_year = date(today.year, month, 28)  # e.g. Feb 29 in a non-leap year
    if this_year < today:
        try:
            return date(today.year + 1, month, day)
        except ValueError:
            return date(today.year + 1, month, 28)
    return this_year


def parse_and_validate_date(text: str):
    """Parses DD.MM.YYYY or DD.MM. (without year). Returns (day, month, year|None) or None."""
    match = DATE_PATTERN.match(text)
    if not match:
        return None
    day_str, month_str, year_str = match.groups()
    day, month = int(day_str), int(month_str)

    if year_str:
        try:
            year = parse_year(year_str)
            datetime(year, month, day)
        except ValueError:
            return None
    else:
        year = None
        try:
            datetime(2000, month, day)  # placeholder leap year to allow Feb 29 without a year
        except ValueError:
            return None

    return day, month, year


def format_date(info: dict) -> str:
    if info["year"] is not None:
        return f"{info['day']:02d}.{info['month']:02d}.{info['year']}"
    return f"{info['day']:02d}.{info['month']:02d}."


def make_embed(title: str, description: str = "", color: int = EMBED_COLOR) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)


async def get_member_or_none(guild: discord.Guild, user_id: int):
    """Returns the Member object if the person is still on THIS server, otherwise None."""
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        return None
    except discord.HTTPException:
        return guild.get_member(user_id)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")

    if GUILD_ID:
        guild = discord.Object(id=int(GUILD_ID))
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
    else:
        synced = await bot.tree.sync()
    print(f"🔄 {len(synced)} slash commands synced.")

    if not check_birthdays.is_running():
        check_birthdays.start()


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Birthdays are per-server -> there is no server context in DMs
    if message.guild is None:
        await bot.process_commands(message)
        return

    guild_id = message.guild.id
    cfg = await db.get_guild_config(guild_id)
    input_channel_id = cfg["input_channel_id"] if cfg else None

    # Only react in the configured input channel. If none is set, allowed anywhere on the server.
    only_in_input_channel = input_channel_id is not None and message.channel.id != input_channel_id

    if not only_in_input_channel and DATE_PATTERN.match(message.content):
        parsed = parse_and_validate_date(message.content)

        if parsed is None:
            embed = make_embed(
                "❌ Invalid date",
                "Please use the format `DD.MM.YYYY` (e.g. `02.08.2000`) "
                "or without a year: `DD.MM.` / `DD.MM` (e.g. `02.08.` or `02.08`).",
                color=0xE74C3C,
            )
            await message.channel.send(embed=embed)
            return

        day, month, year = parsed
        existing = await db.get_birthday(guild_id, message.author.id)
        new_info = {"day": day, "month": month, "year": year}

        await db.set_birthday(guild_id, message.author.id, day, month, year)

        await message.add_reaction("🎂")

        if existing and (existing["day"], existing["month"], existing["year"]) != (day, month, year):
            embed = make_embed(
                "⚠️ Birthday updated",
                f"{message.author.mention} already had "
                f"**{format_date(existing)}** saved.\n"
                f"I've replaced it with **{format_date(new_info)}**.",
                color=0xF1C40F,
            )
        elif existing:
            embed = make_embed(
                "ℹ️ Already saved",
                f"This birthday is already saved for {message.author.mention} "
                f"(**{format_date(new_info)}**) - nothing changed.",
                color=0x3498DB,
            )
        else:
            embed = make_embed(
                "🎂 Birthday saved",
                f"Got it, {message.author.mention}! I've saved your birthday "
                f"as **{format_date(new_info)}**. I'll announce it every year on this day! 🎉",
                color=0x2ECC71,
            )
        await message.channel.send(embed=embed)
        return

    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# Daily check (across ALL configured servers, each handled separately)
# ---------------------------------------------------------------------------


@tasks.loop(hours=24)
async def check_birthdays():
    today = date.today()
    guild_configs = await db.get_all_birthday_channels()

    for cfg in guild_configs:
        guild_id = cfg["guild_id"]
        channel = bot.get_channel(cfg["birthday_channel_id"])
        if channel is None:
            print(f"⚠️  Announcement channel for server {guild_id} not found - skipped.")
            continue

        todays = await db.get_birthdays_on_date(guild_id, today.day, today.month)
        for info in todays:
            member = await get_member_or_none(channel.guild, info["user_id"])
            if member is None:
                print(f"ℹ️  User {info['user_id']} is no longer on server {guild_id} - skipped.")
                continue

            if info["year"] is not None:
                age = today.year - info["year"]
                description = f"Today is {member.mention}'s birthday, turning **{age}**! 🎈🥳"
            else:
                description = f"Today is {member.mention}'s birthday! 🎈🥳"

            embed = make_embed("🎉 Happy Birthday! 🎂", description, color=0x2ECC71)
            await channel.send(embed=embed)


@check_birthdays.before_loop
async def before_check_birthdays():
    await bot.wait_until_ready()
    now = datetime.now()
    target = datetime.combine(now.date(), dtime(hour=ANNOUNCE_HOUR, minute=ANNOUNCE_MINUTE))
    if target < now:
        target += timedelta(days=1)
    await asyncio.sleep((target - now).total_seconds())


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------


@bot.tree.command(name="birthdays", description="Shows the next upcoming birthdays")
@app_commands.describe(count="How many birthdays to show (default: 10)")
@app_commands.guild_only()
async def birthdays(interaction: discord.Interaction, count: int = 10):
    await interaction.response.defer()

    rows = await db.get_all_birthdays(interaction.guild_id)
    entries = []
    for row in rows:
        member = await get_member_or_none(interaction.guild, row["user_id"])
        if member is None:
            continue  # person is no longer on the server -> don't show
        nxt = next_occurrence(row["day"], row["month"])
        days_left = (nxt - date.today()).days
        entries.append((days_left, nxt, row, member))

    if not entries:
        embed = make_embed(
            "🎉 Upcoming birthdays",
            "No birthdays saved on this server yet. "
            "Just type a date like `02.08.2000` (with year) or `02.08.` (without year) in the chat!",
        )
        await interaction.followup.send(embed=embed)
        return

    entries.sort(key=lambda x: x[0])

    lines = []
    for days_left, nxt, info, member in entries[: max(1, count)]:
        age_suffix = f" (turning {nxt.year - info['year']})" if info["year"] is not None else ""
        if days_left == 0:
            lines.append(f"🎂 {member.mention}'s birthday is **today**!{age_suffix}")
        elif days_left == 1:
            lines.append(f"📅 {member.mention}: tomorrow ({nxt.strftime('%d.%m.')}){age_suffix}")
        else:
            lines.append(f"📅 {member.mention}: {nxt.strftime('%d.%m.')} (in {days_left} days){age_suffix}")

    embed = make_embed("🎉 Upcoming birthdays", "\n".join(lines))
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="birthday", description="Shows a person's saved birthday")
@app_commands.describe(person="The person whose birthday to show (default: yourself)")
@app_commands.guild_only()
async def birthday(interaction: discord.Interaction, person: discord.Member = None):
    member = person or interaction.user
    info = await db.get_birthday(interaction.guild_id, member.id)
    if not info:
        embed = make_embed("🎂 Birthday", f"No birthday saved for {member.mention}.", color=0xE74C3C)
    else:
        embed = make_embed("🎂 Birthday", f"{member.mention}'s birthday: {format_date(info)}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="deletebirthday", description="Deletes your own saved birthday on this server")
@app_commands.guild_only()
async def deletebirthday(interaction: discord.Interaction):
    deleted = await db.delete_birthday(interaction.guild_id, interaction.user.id)
    if deleted:
        embed = make_embed(
            "🗑️ Birthday deleted",
            "Your birthday has been removed. You will no longer be announced.",
            color=0x2ECC71,
        )
    else:
        embed = make_embed(
            "ℹ️ Nothing to delete",
            "You didn't have a birthday saved on this server.",
            color=0x3498DB,
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="birthdaystatus", description="Shows this server's current channel configuration")
@app_commands.guild_only()
async def birthdaystatus(interaction: discord.Interaction):
    cfg = await db.get_guild_config(interaction.guild_id)
    birthday_channel_id = cfg["birthday_channel_id"] if cfg else None
    input_channel_id = cfg["input_channel_id"] if cfg else None

    announcement = f"<#{birthday_channel_id}>" if birthday_channel_id else "❌ not set yet"
    input_ch = f"<#{input_channel_id}>" if input_channel_id else "⚠️ not set (detection active in every channel)"

    count = len(await db.get_all_birthdays(interaction.guild_id))

    embed = make_embed("⚙️ Birthday Bot – Server Configuration")
    embed.add_field(name="Announcement channel", value=announcement, inline=False)
    embed.add_field(name="Input channel", value=input_ch, inline=False)
    embed.add_field(name="Birthdays saved on this server", value=str(count), inline=False)
    if not birthday_channel_id:
        embed.color = 0xE74C3C
        embed.description = (
            "⚠️ Without an announcement channel the bot won't post birthday messages **anywhere**! "
            "Set one with `/setbirthdaychannel`."
        )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="setinputchannel", description="Sets the channel where birthdays are captured via typed dates")
@app_commands.describe(channel="Channel (default: the current channel)")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def setinputchannel(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    await db.set_input_channel(interaction.guild_id, channel.id)
    embed = make_embed(
        "✅ Input channel set",
        f"Birthdays will now only be captured when a date is written in {channel.mention}.",
        color=0x2ECC71,
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="setbirthdaychannel", description="Sets the channel where birthday announcements are posted")
@app_commands.describe(channel="Channel (default: the current channel)")
@app_commands.guild_only()
@app_commands.checks.has_permissions(manage_guild=True)
async def setbirthdaychannel(interaction: discord.Interaction, channel: discord.TextChannel = None):
    channel = channel or interaction.channel
    await db.set_birthday_channel(interaction.guild_id, channel.id)
    embed = make_embed(
        "✅ Announcement channel set",
        f"Birthday announcements will now be posted in {channel.mention}.",
        color=0x2ECC71,
    )
    await interaction.response.send_message(embed=embed)


async def _missing_permissions_handler(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        embed = make_embed(
            "🚫 Missing permission",
            "This command requires the 'Manage Server' permission.",
            color=0xE74C3C,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


setinputchannel.error(_missing_permissions_handler)
setbirthdaychannel.error(_missing_permissions_handler)


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------


async def main():
    async with bot:
        await db.init_pool()
        try:
            await bot.start(TOKEN)
        finally:
            await db.close_pool()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("No DISCORD_TOKEN found. Please set it in the .env file.")
    asyncio.run(main())
