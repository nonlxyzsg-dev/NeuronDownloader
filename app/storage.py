import os
import sqlite3
import uuid

from app.config import DATA_DIR, DB_FILENAME


class Storage:
    def __init__(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        self.db_path = os.path.join(DATA_DIR, DB_FILENAME)
        self._init_db()

    def _ensure_db(self) -> None:
        if not os.path.exists(self.db_path):
            self._init_db()
            return
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
            )
            if cur.fetchone() is None:
                self._init_db()

    def _connect(self) -> sqlite3.Connection:
        self._ensure_db()
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_requests (
                    token TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    title TEXT,
                    description TEXT,
                    channel_url TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id INTEGER NOT NULL,
                    channel_url TEXT NOT NULL,
                    resolution TEXT,
                    last_video_id TEXT,
                    PRIMARY KEY (user_id, channel_url)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    blocked INTEGER DEFAULT 0,
                    last_inline_message_id INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    platform TEXT,
                    status TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_limits (
                    user_id INTEGER NOT NULL,
                    date TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (user_id, date)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS free_downloads (
                    user_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS subscription_actions (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    channel_url TEXT NOT NULL
                )
                """
            )

    def create_request(self, url: str, title: str, description: str, channel_url: str | None) -> str:
        token = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO pending_requests (token, url, title, description, channel_url) VALUES (?, ?, ?, ?, ?)",
                (token, url, title, description, channel_url),
            )
        return token

    def get_request(self, token: str) -> tuple[str, str, str, str | None] | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT url, title, description, channel_url FROM pending_requests WHERE token = ?",
                (token,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return row[0], row[1] or "", row[2] or "", row[3]

    def delete_request(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM pending_requests WHERE token = ?", (token,))

    def upsert_subscription(self, user_id: int, channel_url: str, resolution: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO subscriptions (user_id, channel_url, resolution, last_video_id)
                VALUES (?, ?, ?, NULL)
                ON CONFLICT(user_id, channel_url)
                DO UPDATE SET resolution = excluded.resolution
                """,
                (user_id, channel_url, resolution),
            )

    def remove_subscription(self, user_id: int, channel_url: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM subscriptions WHERE user_id = ? AND channel_url = ?",
                (user_id, channel_url),
            )

    def list_subscriptions(self) -> list[tuple[int, str, str | None, str | None]]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT user_id, channel_url, resolution, last_video_id FROM subscriptions"
            )
            return cur.fetchall()

    def update_last_video(self, user_id: int, channel_url: str, last_video_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE subscriptions SET last_video_id = ? WHERE user_id = ? AND channel_url = ?",
                (last_video_id, user_id, channel_url),
            )

    def upsert_user(self, user_id: int, username: str, first_name: str, last_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, last_name, blocked, last_inline_message_id)
                VALUES (?, ?, ?, ?, 0, NULL)
                ON CONFLICT(user_id)
                DO UPDATE SET username = excluded.username,
                              first_name = excluded.first_name,
                              last_name = excluded.last_name
                """,
                (user_id, username, first_name, last_name),
            )

    def is_blocked(self, user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT blocked FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = cur.fetchone()
        return bool(row and row[0])

    def set_blocked(self, user_id: int, blocked: bool) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET blocked = ? WHERE user_id = ?",
                (1 if blocked else 0, user_id),
            )

    def list_users(self) -> list[tuple[int, str, str, str, int]]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT user_id, username, first_name, last_name, blocked FROM users ORDER BY user_id"
            )
            return cur.fetchall()

    def get_last_inline_message_id(self, user_id: int) -> int | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT last_inline_message_id FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return row[0]

    def set_last_inline_message_id(self, user_id: int, message_id: int | None) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_inline_message_id = ? WHERE user_id = ?",
                (message_id, user_id),
            )

    def log_download(self, user_id: int, platform: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO downloads (user_id, platform, status) VALUES (?, ?, ?)",
                (user_id, platform, status),
            )

    def get_daily_downloads(self, user_id: int, date_value: str) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT count FROM daily_limits WHERE user_id = ? AND date = ?",
                (user_id, date_value),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def increment_daily_downloads(self, user_id: int, date_value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_limits (user_id, date, count)
                VALUES (?, ?, 1)
                ON CONFLICT(user_id, date)
                DO UPDATE SET count = count + 1
                """,
                (user_id, date_value),
            )

    def log_free_download(self, user_id: int, created_at: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO free_downloads (user_id, created_at) VALUES (?, ?)",
                (user_id, created_at),
            )

    def count_free_downloads_since(self, user_id: int, start_ts: int) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM free_downloads WHERE user_id = ? AND created_at >= ?",
                (user_id, start_ts),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    def get_usage_stats(self) -> tuple[int, int]:
        with self._connect() as conn:
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total_downloads = conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
        return int(total_users), int(total_downloads)

    def get_user_stats(self) -> list[tuple[int, int]]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT user_id, COUNT(*) FROM downloads GROUP BY user_id ORDER BY COUNT(*) DESC"
            )
            return cur.fetchall()

    def list_user_subscriptions(self, user_id: int) -> list[tuple[str, str | None]]:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT channel_url, resolution FROM subscriptions WHERE user_id = ?",
                (user_id,),
            )
            return cur.fetchall()

    def create_subscription_action(self, user_id: int, channel_url: str) -> str:
        token = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO subscription_actions (token, user_id, channel_url) VALUES (?, ?, ?)",
                (token, user_id, channel_url),
            )
        return token

    def get_subscription_action(self, token: str) -> tuple[int, str] | None:
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT user_id, channel_url FROM subscription_actions WHERE token = ?",
                (token,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return row[0], row[1]

    def delete_subscription_action(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM subscription_actions WHERE token = ?",
                (token,),
            )
