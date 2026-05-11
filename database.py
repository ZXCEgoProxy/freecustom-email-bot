import aiosqlite
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any
from config import Config

# PostgreSQL support
try:
    import asyncpg
    POSTGRES_AVAILABLE = True
except ImportError:
    POSTGRES_AVAILABLE = False

class BaseDatabase:
    """Base database interface"""

    async def init_db(self):
        raise NotImplementedError

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def save_user(self, user_id: int, api_key: str):
        raise NotImplementedError

    async def delete_user(self, user_id: int):
        raise NotImplementedError

    async def save_inbox(self, user_id: int, email: str, expires_at: Optional[datetime] = None) -> int:
        raise NotImplementedError

    async def get_user_inboxes(self, user_id: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_inbox(self, inbox_id: int) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def update_inbox_last_checked(self, inbox_id: int):
        raise NotImplementedError

    async def delete_inbox(self, inbox_id: int):
        raise NotImplementedError

    async def save_message(self, inbox_id: int, message_data: Dict[str, Any]):
        raise NotImplementedError

    async def get_inbox_messages(self, inbox_id: int) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def get_message(self, message_id: int) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    async def mark_message_read(self, message_id: int):
        raise NotImplementedError

    async def get_expiring_inboxes(self, minutes_ahead: int = 5) -> List[Dict[str, Any]]:
        raise NotImplementedError

class SQLiteDatabase(BaseDatabase):
    def __init__(self, db_path: str = Config.DATABASE_PATH):
        self.db_path = db_path

    async def init_db(self):
        """Initialize database with required tables"""
        async with aiosqlite.connect(self.db_path) as db:
            # Users table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    api_key TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Inboxes table (cache/management)
            await db.execute('''
                CREATE TABLE IF NOT EXISTS inboxes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'active',  -- active, archived, deleted
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')

            # Messages table to cache emails
            await db.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inbox_id INTEGER NOT NULL,
                    message_id TEXT NOT NULL,
                    subject TEXT,
                    sender TEXT,
                    received_at TIMESTAMP,
                    body_html TEXT,
                    body_text TEXT,
                    is_read BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY (inbox_id) REFERENCES inboxes (id)
                )
            ''')

            await db.commit()

    # User operations
    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM users WHERE user_id = ?', (user_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def save_user(self, user_id: int, api_key: str):
        """Save or update user with API key"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT OR REPLACE INTO users (user_id, api_key, created_at)
                VALUES (?, ?, ?)
            ''', (user_id, api_key, datetime.now()))
            await db.commit()

    async def delete_user(self, user_id: int):
        """Delete user and all their data"""
        async with aiosqlite.connect(self.db_path) as db:
            # Delete messages first (foreign key constraint)
            await db.execute('DELETE FROM messages WHERE inbox_id IN (SELECT id FROM inboxes WHERE user_id = ?)', (user_id,))
            await db.execute('DELETE FROM inboxes WHERE user_id = ?', (user_id,))
            await db.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
            await db.commit()

    # Inbox operations
    async def save_inbox(self, user_id: int, email: str, expires_at: Optional[datetime] = None) -> int:
        """Save inbox and return its ID"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute('''
                INSERT INTO inboxes (user_id, email, expires_at, status)
                VALUES (?, ?, ?, 'active')
            ''', (user_id, email, expires_at))
            inbox_id = cursor.lastrowid
            await db.commit()
            return inbox_id

    async def get_user_inboxes(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all active inboxes for user"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM inboxes
                WHERE user_id = ? AND status = 'active'
                ORDER BY created_at DESC
            ''', (user_id,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_inbox(self, inbox_id: int) -> Optional[Dict[str, Any]]:
        """Get inbox by ID"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM inboxes WHERE id = ?', (inbox_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def update_inbox_last_checked(self, inbox_id: int):
        """Update last checked timestamp"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                UPDATE inboxes SET last_checked = ? WHERE id = ?
            ''', (datetime.now(), inbox_id))
            await db.commit()

    async def delete_inbox(self, inbox_id: int):
        """Mark inbox as deleted"""
        async with aiosqlite.connect(self.db_path) as db:
            # Delete messages first
            await db.execute('DELETE FROM messages WHERE inbox_id = ?', (inbox_id,))
            await db.execute("UPDATE inboxes SET status = 'deleted' WHERE id = ?", (inbox_id,))
            await db.commit()

    # Message operations
    async def save_message(self, inbox_id: int, message_data: Dict[str, Any]):
        """Save message to cache"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('''
                INSERT OR IGNORE INTO messages
                (inbox_id, message_id, subject, sender, received_at, body_html, body_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                inbox_id,
                message_data.get('id'),
                message_data.get('subject'),
                message_data.get('from'),
                message_data.get('date'),
                message_data.get('body_html'),
                message_data.get('body_text')
            ))
            await db.commit()

    async def get_inbox_messages(self, inbox_id: int) -> List[Dict[str, Any]]:
        """Get all messages for an inbox"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM messages
                WHERE inbox_id = ?
                ORDER BY received_at DESC
            ''', (inbox_id,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_message(self, message_id: int) -> Optional[Dict[str, Any]]:
        """Get message by ID"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('SELECT * FROM messages WHERE id = ?', (message_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def mark_message_read(self, message_id: int):
        """Mark message as read"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute('UPDATE messages SET is_read = TRUE WHERE id = ?', (message_id,))
            await db.commit()

    # Utility methods
    async def get_expiring_inboxes(self, minutes_ahead: int = 5) -> List[Dict[str, Any]]:
        """Get inboxes that will expire soon"""
        from datetime import timedelta
        expiry_time = datetime.now() + timedelta(minutes=minutes_ahead)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute('''
                SELECT * FROM inboxes
                WHERE status = 'active' AND expires_at IS NOT NULL
                AND expires_at <= ? AND expires_at > ?
            ''', (expiry_time, datetime.now())) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

class PostgreSQLDatabase(BaseDatabase):
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None

    async def _get_connection(self):
        """Get database connection from pool"""
        if not self.pool:
            self.pool = await asyncpg.create_pool(self.database_url)
        return await self.pool.acquire()

    async def _release_connection(self, conn):
        """Release connection back to pool"""
        if self.pool:
            await self.pool.release(conn)

    async def init_db(self):
        """Initialize PostgreSQL database with required tables"""
        conn = await self._get_connection()
        try:
            # Users table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    api_key TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Inboxes table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS inboxes (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    email TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'active'
                )
            ''')

            # Messages table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    inbox_id INTEGER NOT NULL REFERENCES inboxes(id) ON DELETE CASCADE,
                    message_id TEXT NOT NULL,
                    subject TEXT,
                    sender TEXT,
                    received_at TIMESTAMP,
                    body_html TEXT,
                    body_text TEXT,
                    is_read BOOLEAN DEFAULT FALSE
                )
            ''')

        finally:
            await self._release_connection(conn)

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user by ID"""
        conn = await self._get_connection()
        try:
            row = await conn.fetchrow('SELECT * FROM users WHERE user_id = $1', user_id)
            return dict(row) if row else None
        finally:
            await self._release_connection(conn)

    async def save_user(self, user_id: int, api_key: str):
        """Save or update user with API key"""
        conn = await self._get_connection()
        try:
            await conn.execute('''
                INSERT INTO users (user_id, api_key, created_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET
                    api_key = EXCLUDED.api_key,
                    created_at = EXCLUDED.created_at
            ''', user_id, api_key, datetime.now())
        finally:
            await self._release_connection(conn)

    async def delete_user(self, user_id: int):
        """Delete user and all their data (CASCADE will handle related tables)"""
        conn = await self._get_connection()
        try:
            await conn.execute('DELETE FROM users WHERE user_id = $1', user_id)
        finally:
            await self._release_connection(conn)

    async def save_inbox(self, user_id: int, email: str, expires_at: Optional[datetime] = None) -> int:
        """Save inbox and return its ID"""
        conn = await self._get_connection()
        try:
            result = await conn.fetchval('''
                INSERT INTO inboxes (user_id, email, expires_at, status)
                VALUES ($1, $2, $3, 'active')
                RETURNING id
            ''', user_id, email, expires_at)
            return result
        finally:
            await self._release_connection(conn)

    async def get_user_inboxes(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all active inboxes for user"""
        conn = await self._get_connection()
        try:
            rows = await conn.fetch('''
                SELECT * FROM inboxes
                WHERE user_id = $1 AND status = 'active'
                ORDER BY created_at DESC
            ''', user_id)
            return [dict(row) for row in rows]
        finally:
            await self._release_connection(conn)

    async def get_inbox(self, inbox_id: int) -> Optional[Dict[str, Any]]:
        """Get inbox by ID"""
        conn = await self._get_connection()
        try:
            row = await conn.fetchrow('SELECT * FROM inboxes WHERE id = $1', inbox_id)
            return dict(row) if row else None
        finally:
            await self._release_connection(conn)

    async def update_inbox_last_checked(self, inbox_id: int):
        """Update last checked timestamp"""
        conn = await self._get_connection()
        try:
            await conn.execute('''
                UPDATE inboxes SET last_checked = $1 WHERE id = $2
            ''', datetime.now(), inbox_id)
        finally:
            await self._release_connection(conn)

    async def delete_inbox(self, inbox_id: int):
        """Mark inbox as deleted"""
        conn = await self._get_connection()
        try:
            await conn.execute("UPDATE inboxes SET status = 'deleted' WHERE id = $1", inbox_id)
        finally:
            await self._release_connection(conn)

    async def save_message(self, inbox_id: int, message_data: Dict[str, Any]):
        """Save message to cache"""
        conn = await self._get_connection()
        try:
            await conn.execute('''
                INSERT INTO messages
                (inbox_id, message_id, subject, sender, received_at, body_html, body_text)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT DO NOTHING
            ''', (
                inbox_id,
                message_data.get('id'),
                message_data.get('subject'),
                message_data.get('from'),
                message_data.get('date'),
                message_data.get('body_html'),
                message_data.get('body_text')
            ))
        finally:
            await self._release_connection(conn)

    async def get_inbox_messages(self, inbox_id: int) -> List[Dict[str, Any]]:
        """Get all messages for an inbox"""
        conn = await self._get_connection()
        try:
            rows = await conn.fetch('''
                SELECT * FROM messages
                WHERE inbox_id = $1
                ORDER BY received_at DESC
            ''', inbox_id)
            return [dict(row) for row in rows]
        finally:
            await self._release_connection(conn)

    async def get_message(self, message_id: int) -> Optional[Dict[str, Any]]:
        """Get message by ID"""
        conn = await self._get_connection()
        try:
            row = await conn.fetchrow('SELECT * FROM messages WHERE id = $1', message_id)
            return dict(row) if row else None
        finally:
            await self._release_connection(conn)

    async def mark_message_read(self, message_id: int):
        """Mark message as read"""
        conn = await self._get_connection()
        try:
            await conn.execute('UPDATE messages SET is_read = TRUE WHERE id = $1', message_id)
        finally:
            await self._release_connection(conn)

    async def get_expiring_inboxes(self, minutes_ahead: int = 5) -> List[Dict[str, Any]]:
        """Get inboxes that will expire soon"""
        from datetime import timedelta
        expiry_time = datetime.now() + timedelta(minutes=minutes_ahead)
        conn = await self._get_connection()
        try:
            rows = await conn.fetch('''
                SELECT * FROM inboxes
                WHERE status = 'active' AND expires_at IS NOT NULL
                AND expires_at <= $1 AND expires_at > $2
            ''', expiry_time, datetime.now())
            return [dict(row) for row in rows]
        finally:
            await self._release_connection(conn)

    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()

# Factory function to create appropriate database instance
def create_database():
    """Create database instance based on configuration"""
    if Config.USE_POSTGRESQL:
        if not POSTGRES_AVAILABLE:
            raise RuntimeError("PostgreSQL support not available. Install asyncpg: pip install asyncpg")
        return PostgreSQLDatabase(Config.DATABASE_URL)
    else:
        return SQLiteDatabase()

# Global database instance
db = create_database()

async def init_database():
    """Initialize database on startup"""
    await db.init_db()

async def close_database():
    """Close database connections on shutdown"""
    if hasattr(db, 'close'):
        await db.close()