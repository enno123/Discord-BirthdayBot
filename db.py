"""
Database access layer (MySQL) for the birthday bot.

All data is stored strictly separated per server (guild_id):
- Table 'birthdays'    -> birthdays per server + user
- Table 'guild_config' -> input/announcement channel per server

Tables are created automatically on first start (CREATE TABLE IF NOT EXISTS).
"""

import os
import aiomysql

_pool: aiomysql.Pool | None = None


async def init_pool() -> None:
    """Creates the connection pool and ensures the tables exist."""
    global _pool
    _pool = await aiomysql.create_pool(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        db=os.getenv("DB_NAME"),
        autocommit=True,
        minsize=1,
        maxsize=5,
    )
    print(f"✅ Connected to MySQL database '{os.getenv('DB_NAME')}' on {os.getenv('DB_HOST')}.")
    await _create_tables()


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        await _pool.wait_closed()
        _pool = None


async def _create_tables() -> None:
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SHOW TABLES LIKE 'birthdays'")
            birthdays_existed = (await cur.fetchone()) is not None
            await cur.execute("SHOW TABLES LIKE 'guild_config'")
            guild_config_existed = (await cur.fetchone()) is not None

            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS birthdays (
                    guild_id BIGINT UNSIGNED NOT NULL,
                    user_id  BIGINT UNSIGNED NOT NULL,
                    day      TINYINT UNSIGNED NOT NULL,
                    month    TINYINT UNSIGNED NOT NULL,
                    year     SMALLINT UNSIGNED NULL,
                    PRIMARY KEY (guild_id, user_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id             BIGINT UNSIGNED PRIMARY KEY,
                    birthday_channel_id  BIGINT UNSIGNED NULL,
                    input_channel_id     BIGINT UNSIGNED NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )

    if birthdays_existed and guild_config_existed:
        print("ℹ️  Tables 'birthdays' and 'guild_config' already existed - nothing changed.")
    else:
        new_tables = []
        if not birthdays_existed:
            new_tables.append("birthdays")
        if not guild_config_existed:
            new_tables.append("guild_config")
        print(f"🆕 Newly created table(s): {', '.join(new_tables)}")


# ---------------------------------------------------------------------------
# Birthdays
# ---------------------------------------------------------------------------


async def get_birthday(guild_id: int, user_id: int) -> dict | None:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT day, month, year FROM birthdays WHERE guild_id=%s AND user_id=%s",
                (guild_id, user_id),
            )
            return await cur.fetchone()


async def set_birthday(guild_id: int, user_id: int, day: int, month: int, year: int | None) -> None:
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO birthdays (guild_id, user_id, day, month, year)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE day=VALUES(day), month=VALUES(month), year=VALUES(year)
                """,
                (guild_id, user_id, day, month, year),
            )


async def delete_birthday(guild_id: int, user_id: int) -> bool:
    """Deletes a birthday. Returns True if a row was actually deleted."""
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM birthdays WHERE guild_id=%s AND user_id=%s",
                (guild_id, user_id),
            )
            return cur.rowcount > 0


async def get_all_birthdays(guild_id: int) -> list[dict]:
    """All birthdays of a server, each including user_id."""
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT user_id, day, month, year FROM birthdays WHERE guild_id=%s",
                (guild_id,),
            )
            return await cur.fetchall()


async def get_birthdays_on_date(guild_id: int, day: int, month: int) -> list[dict]:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT user_id, day, month, year FROM birthdays "
                "WHERE guild_id=%s AND day=%s AND month=%s",
                (guild_id, day, month),
            )
            return await cur.fetchall()


# ---------------------------------------------------------------------------
# Server configuration (channels)
# ---------------------------------------------------------------------------


async def get_guild_config(guild_id: int) -> dict | None:
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT birthday_channel_id, input_channel_id FROM guild_config WHERE guild_id=%s",
                (guild_id,),
            )
            return await cur.fetchone()


async def set_birthday_channel(guild_id: int, channel_id: int) -> None:
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO guild_config (guild_id, birthday_channel_id) VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE birthday_channel_id=VALUES(birthday_channel_id)
                """,
                (guild_id, channel_id),
            )


async def set_input_channel(guild_id: int, channel_id: int) -> None:
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO guild_config (guild_id, input_channel_id) VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE input_channel_id=VALUES(input_channel_id)
                """,
                (guild_id, channel_id),
            )


async def get_all_birthday_channels() -> list[dict]:
    """All servers that have an announcement channel configured (used by the daily check)."""
    async with _pool.acquire() as conn:
        async with conn.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(
                "SELECT guild_id, birthday_channel_id FROM guild_config "
                "WHERE birthday_channel_id IS NOT NULL"
            )
            return await cur.fetchall()
