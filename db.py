from __future__ import annotations
import asyncio
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from config import DB_PATH


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = asyncio.Lock()
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self.path) as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                balance REAL DEFAULT 0,
                spent REAL DEFAULT 0,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS packs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                pack_name TEXT NOT NULL,
                title TEXT NOT NULL,
                pack_type TEXT NOT NULL,
                link TEXT NOT NULL,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                method TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                external_id TEXT,
                created_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_packs_user ON packs(user_id);
            """)
            c.commit()

    def _run(self, fn):
        def wrapper():
            with sqlite3.connect(self.path) as c:
                c.row_factory = sqlite3.Row
                return fn(c)
        return wrapper

    async def _exec(self, fn):
        async with self.lock:
            return await asyncio.to_thread(self._run(fn))

    async def ensure_user(self, uid: int, first_name: str, username: Optional[str]) -> None:
        def go(c):
            c.execute(
                "INSERT OR IGNORE INTO users (id, first_name, username, created_at) VALUES (?,?,?,?)",
                (uid, first_name, username, datetime.now().strftime("%d.%m.%Y")),
            )
            c.execute(
                "UPDATE users SET first_name=?, username=? WHERE id=?",
                (first_name, username, uid),
            )
            c.commit()
        await self._exec(go)

    async def get_user(self, uid: int) -> Dict[str, Any]:
        def go(c):
            r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            return dict(r) if r else {}
        return await self._exec(go)

    async def add_balance(self, uid: int, amount: float) -> None:
        def go(c):
            c.execute("UPDATE users SET balance = balance + ? WHERE id=?", (amount, uid))
            c.commit()
        await self._exec(go)

    async def try_spend(self, uid: int, amount: float) -> bool:
        def go(c):
            cur = c.execute(
                "UPDATE users SET balance = balance - ?, spent = spent + ? "
                "WHERE id=? AND balance >= ?",
                (amount, amount, uid, amount),
            )
            c.commit()
            return cur.rowcount > 0
        return await self._exec(go)

    async def refund(self, uid: int, amount: float) -> None:
        def go(c):
            c.execute(
                "UPDATE users SET balance = balance + ?, spent = MAX(0, spent - ?) WHERE id=?",
                (amount, amount, uid),
            )
            c.commit()
        await self._exec(go)

    async def add_pack(self, uid: int, name: str, title: str, ptype: str, link: str) -> None:
        def go(c):
            c.execute(
                "INSERT INTO packs (user_id, pack_name, title, pack_type, link, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (uid, name, title, ptype, link, datetime.now().strftime("%d.%m.%Y %H:%M")),
            )
            c.commit()
        await self._exec(go)

    async def get_packs(self, uid: int) -> List[Dict[str, Any]]:
        def go(c):
            rows = c.execute(
                "SELECT * FROM packs WHERE user_id=? ORDER BY id DESC", (uid,)
            ).fetchall()
            return [dict(r) for r in rows]
        return await self._exec(go)

    async def get_pack_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        def go(c):
            r = c.execute("SELECT * FROM packs WHERE pack_name=?", (name,)).fetchone()
            return dict(r) if r else None
        return await self._exec(go)

    async def rename_pack(self, uid: int, pack_name: str, new_title: str) -> bool:
        def go(c):
            cur = c.execute(
                "UPDATE packs SET title=? WHERE user_id=? AND pack_name=?",
                (new_title, uid, pack_name),
            )
            c.commit()
            return cur.rowcount > 0
        return await self._exec(go)

    async def create_payment(self, uid: int, amount: int, method: str, ext: str) -> int:
        def go(c):
            cur = c.execute(
                "INSERT INTO payments (user_id, amount, method, status, external_id, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (uid, amount, method, "pending", ext,
                 datetime.now().strftime("%d.%m.%Y %H:%M")),
            )
            c.commit()
            return cur.lastrowid
        return await self._exec(go)

    async def complete_payment_if_pending(self, payment_id: int) -> bool:
        def go(c):
            cur = c.execute(
                "UPDATE payments SET status='completed' WHERE id=? AND status='pending'",
                (payment_id,),
            )
            c.commit()
            return cur.rowcount > 0
        return await self._exec(go)


db = Database(DB_PATH)