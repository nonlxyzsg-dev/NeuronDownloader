"""SQLite-хранилище: пользователи, загрузки, тикеты, настройки."""

import os
import sqlite3
import uuid

from app.config import DATA_DIR, DB_FILENAME


class Storage:
    """Хранилище данных бота на SQLite."""

    def __init__(self) -> None:
        os.makedirs(DATA_DIR, exist_ok=True)
        self.db_path = os.path.join(DATA_DIR, DB_FILENAME)
        self._init_db()
        self._migrate_db()

    def _ensure_db(self) -> None:
        """Проверяет наличие БД и таблиц, при необходимости создаёт."""
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
        """Возвращает соединение с БД."""
        self._ensure_db()
        return sqlite3.connect(self.db_path)

    def _migrate_db(self) -> None:
        """Добавляет недостающие колонки (миграции)."""
        with sqlite3.connect(self.db_path) as conn:
            # device_type в users
            cols = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "device_type" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN device_type TEXT")

    def _init_db(self) -> None:
        """Создаёт все таблицы БД."""
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
                CREATE TABLE IF NOT EXISTS free_downloads (
                    user_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            # --- Система поддержки ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS support_tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'open',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS support_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id INTEGER NOT NULL,
                    from_user_id INTEGER NOT NULL,
                    is_admin INTEGER DEFAULT 0,
                    text TEXT,
                    file_id TEXT,
                    file_type TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (ticket_id) REFERENCES support_tickets(id)
                )
                """
            )
            # --- Обязательные каналы (управляются через админку) ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS required_channels (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT,
                    invite_link TEXT
                )
                """
            )
            # --- Инциденты воспроизведения видео ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS video_incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    url TEXT,
                    platform TEXT,
                    format_id TEXT,
                    codec TEXT,
                    resolution TEXT,
                    file_size INTEGER,
                    status TEXT DEFAULT 'reported',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    resolved_at TIMESTAMP
                )
                """
            )
            # --- Динамические настройки бота ---
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    # --- Ожидающие запросы ---

    def create_request(self, url: str, title: str, description: str, channel_url: str | None) -> str:
        """Создаёт запрос на скачивание и возвращает токен."""
        token = uuid.uuid4().hex[:12]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO pending_requests (token, url, title, description, channel_url) VALUES (?, ?, ?, ?, ?)",
                (token, url, title, description, channel_url),
            )
        return token

    def get_request(self, token: str) -> tuple[str, str, str, str | None] | None:
        """Возвращает данные запроса по токену."""
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
        """Удаляет запрос по токену."""
        with self._connect() as conn:
            conn.execute("DELETE FROM pending_requests WHERE token = ?", (token,))

    # --- Пользователи ---

    def upsert_user(self, user_id: int, username: str, first_name: str, last_name: str) -> None:
        """Создаёт или обновляет данные пользователя."""
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
        """Проверяет, заблокирован ли пользователь."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT blocked FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = cur.fetchone()
        return bool(row and row[0])

    def set_blocked(self, user_id: int, blocked: bool) -> None:
        """Устанавливает статус блокировки пользователя."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET blocked = ? WHERE user_id = ?",
                (1 if blocked else 0, user_id),
            )

    def list_users(self) -> list[tuple[int, str, str, str, int]]:
        """Возвращает список всех пользователей."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT user_id, username, first_name, last_name, blocked FROM users ORDER BY user_id"
            )
            return cur.fetchall()

    def get_user(self, user_id: int) -> tuple[int, str, str, str, int] | None:
        """Возвращает данные пользователя по ID."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT user_id, username, first_name, last_name, blocked FROM users WHERE user_id = ?",
                (user_id,),
            )
            return cur.fetchone()

    def count_users(self) -> int:
        """Возвращает общее количество пользователей."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    def get_last_inline_message_id(self, user_id: int) -> int | None:
        """Возвращает ID последнего инлайн-сообщения пользователя."""
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
        """Сохраняет ID последнего инлайн-сообщения пользователя."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_inline_message_id = ? WHERE user_id = ?",
                (message_id, user_id),
            )

    def get_user_device_type(self, user_id: int) -> str | None:
        """Возвращает тип устройства пользователя ('android', 'iphone' или None)."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT device_type FROM users WHERE user_id = ?",
                (user_id,),
            )
            row = cur.fetchone()
        return row[0] if row else None

    def set_user_device_type(self, user_id: int, device_type: str) -> None:
        """Устанавливает тип устройства пользователя."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET device_type = ? WHERE user_id = ?",
                (device_type, user_id),
            )

    # --- Загрузки ---

    def log_download(self, user_id: int, platform: str, status: str) -> None:
        """Записывает событие загрузки."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO downloads (user_id, platform, status) VALUES (?, ?, ?)",
                (user_id, platform, status),
            )

    def log_free_download(self, user_id: int, created_at: int) -> None:
        """Записывает бесплатную загрузку для учёта лимитов."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO free_downloads (user_id, created_at) VALUES (?, ?)",
                (user_id, created_at),
            )

    def count_free_downloads_since(self, user_id: int, start_ts: int) -> int:
        """Считает бесплатные загрузки пользователя с указанного момента."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM free_downloads WHERE user_id = ? AND created_at >= ?",
                (user_id, start_ts),
            )
            row = cur.fetchone()
        return int(row[0]) if row else 0

    # --- Статистика ---

    def get_usage_stats(self) -> tuple[int, int]:
        """Возвращает общую статистику: (кол-во пользователей, кол-во загрузок)."""
        with self._connect() as conn:
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total_downloads = conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
        return int(total_users), int(total_downloads)

    def get_user_stats(self) -> list[tuple[int, int]]:
        """Возвращает статистику загрузок по пользователям."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT user_id, COUNT(*) FROM downloads GROUP BY user_id ORDER BY COUNT(*) DESC"
            )
            return cur.fetchall()

    def get_user_download_count(self, user_id: int) -> int:
        """Возвращает количество загрузок пользователя."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM downloads WHERE user_id = ?",
                (user_id,),
            )
            return cur.fetchone()[0]

    def get_stats_by_platform(self) -> list[tuple[str, int]]:
        """Возвращает статистику загрузок по платформам."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT COALESCE(platform, 'unknown'), COUNT(*) FROM downloads "
                "GROUP BY platform ORDER BY COUNT(*) DESC"
            )
            return cur.fetchall()

    def get_stats_by_day(self, days: int = 7) -> list[tuple[str, int]]:
        """Возвращает статистику загрузок по дням за последние N дней."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT DATE(created_at) as day, COUNT(*) FROM downloads "
                "WHERE created_at >= datetime('now', ? || ' days') "
                "GROUP BY day ORDER BY day DESC",
                (f"-{days}",),
            )
            return cur.fetchall()

    def get_downloads_today(self) -> int:
        """Возвращает количество загрузок за сегодня."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM downloads WHERE DATE(created_at) = DATE('now')"
            )
            return cur.fetchone()[0]

    def get_downloads_week(self) -> int:
        """Возвращает количество загрузок за последние 7 дней."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM downloads WHERE created_at >= datetime('now', '-7 days')"
            )
            return cur.fetchone()[0]

    # --- Тикеты поддержки ---

    def create_ticket(self, user_id: int) -> int:
        """Создаёт тикет поддержки и возвращает его ID."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO support_tickets (user_id) VALUES (?)",
                (user_id,),
            )
            return cur.lastrowid

    def get_ticket(self, ticket_id: int) -> tuple[int, int, str, str] | None:
        """Возвращает данные тикета по ID."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT id, user_id, status, created_at FROM support_tickets WHERE id = ?",
                (ticket_id,),
            )
            return cur.fetchone()

    def list_open_tickets(self) -> list[tuple[int, int, str, str]]:
        """Возвращает список открытых тикетов."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT t.id, t.user_id, t.status, t.created_at "
                "FROM support_tickets t WHERE t.status = 'open' "
                "ORDER BY t.created_at DESC"
            )
            return cur.fetchall()

    def close_ticket(self, ticket_id: int) -> None:
        """Закрывает тикет."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE support_tickets SET status = 'closed' WHERE id = ?",
                (ticket_id,),
            )

    def add_ticket_message(
        self,
        ticket_id: int,
        from_user_id: int,
        is_admin: bool,
        text: str | None = None,
        file_id: str | None = None,
        file_type: str | None = None,
    ) -> None:
        """Добавляет сообщение к тикету."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO support_messages (ticket_id, from_user_id, is_admin, text, file_id, file_type) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ticket_id, from_user_id, 1 if is_admin else 0, text, file_id, file_type),
            )

    def get_ticket_messages(self, ticket_id: int) -> list[tuple[int, int, int, str | None, str | None, str | None, str]]:
        """Возвращает все сообщения тикета."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT id, from_user_id, is_admin, text, file_id, file_type, created_at "
                "FROM support_messages WHERE ticket_id = ? ORDER BY created_at ASC",
                (ticket_id,),
            )
            return cur.fetchall()

    def count_open_tickets(self) -> int:
        """Возвращает количество открытых тикетов."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM support_tickets WHERE status = 'open'"
            ).fetchone()[0]

    # --- Обязательные каналы ---

    def get_required_channels(self) -> list[tuple[int, str | None, str | None]]:
        """Возвращает список обязательных каналов."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT chat_id, title, invite_link FROM required_channels"
            )
            return cur.fetchall()

    def add_required_channel(self, chat_id: int, title: str | None = None, invite_link: str | None = None) -> None:
        """Добавляет или обновляет обязательный канал."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO required_channels (chat_id, title, invite_link)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = COALESCE(excluded.title, required_channels.title),
                    invite_link = COALESCE(excluded.invite_link, required_channels.invite_link)
                """,
                (chat_id, title, invite_link),
            )

    def remove_required_channel(self, chat_id: int) -> None:
        """Удаляет обязательный канал."""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM required_channels WHERE chat_id = ?",
                (chat_id,),
            )

    # --- Настройки бота ---

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Возвращает значение настройки по ключу."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?",
                (key,),
            )
            row = cur.fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        """Устанавливает значение настройки."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    # --- Инциденты воспроизведения видео ---

    def create_video_incident(
        self,
        user_id: int,
        url: str | None = None,
        platform: str | None = None,
        format_id: str | None = None,
        codec: str | None = None,
        resolution: str | None = None,
        file_size: int | None = None,
    ) -> int:
        """Создаёт инцидент воспроизведения видео и возвращает его ID."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO video_incidents (user_id, url, platform, format_id, codec, resolution, file_size) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, url, platform, format_id, codec, resolution, file_size),
            )
            return cur.lastrowid

    def get_video_incident(self, incident_id: int) -> tuple | None:
        """Возвращает данные инцидента: (id, user_id, url, platform, format_id, codec, resolution, file_size, status, created_at, resolved_at)."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT id, user_id, url, platform, format_id, codec, resolution, file_size, status, created_at, resolved_at "
                "FROM video_incidents WHERE id = ?",
                (incident_id,),
            )
            return cur.fetchone()

    def list_video_incidents(self, status: str | None = None) -> list[tuple]:
        """Возвращает список инцидентов (опционально фильтр по статусу)."""
        with self._connect() as conn:
            if status:
                cur = conn.execute(
                    "SELECT id, user_id, url, platform, format_id, codec, resolution, file_size, status, created_at, resolved_at "
                    "FROM video_incidents WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                )
            else:
                cur = conn.execute(
                    "SELECT id, user_id, url, platform, format_id, codec, resolution, file_size, status, created_at, resolved_at "
                    "FROM video_incidents WHERE status IN ('reported', 'in_progress') ORDER BY created_at DESC"
                )
            return cur.fetchall()

    def set_incident_status(self, incident_id: int, status: str) -> None:
        """Обновляет статус инцидента. При 'fixed'/'wont_fix' устанавливает resolved_at."""
        with self._connect() as conn:
            if status in ("fixed", "wont_fix"):
                conn.execute(
                    "UPDATE video_incidents SET status = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (status, incident_id),
                )
            else:
                conn.execute(
                    "UPDATE video_incidents SET status = ? WHERE id = ?",
                    (status, incident_id),
                )

    def count_open_incidents(self) -> int:
        """Возвращает количество открытых инцидентов (reported + in_progress)."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM video_incidents WHERE status IN ('reported', 'in_progress')"
            ).fetchone()[0]
