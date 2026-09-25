from __future__ import annotations
import asyncio
import json as _json
from typing import Any, Dict, Optional

import aiohttp

from config import BOT_TOKEN

BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_BASE = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

_session: Optional[aiohttp.ClientSession] = None
_lock = asyncio.Lock()
_bot_username_cache: Optional[str] = None


async def session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        async with _lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=120),
                    connector=aiohttp.TCPConnector(limit=32),
                )
    return _session


async def close() -> None:
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None


async def call(method: str, **params: Any) -> Any:
    s = await session()
    async with s.post(f"{BASE}/{method}", json=params) as resp:
        data = await resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API {method}: {data.get('description')}")
    return data["result"]


async def get_me() -> Dict[str, Any]:
    return await call("getMe")


async def get_bot_username() -> str:
    """Возвращает username бота, кэширует после первого вызова."""
    global _bot_username_cache
    if _bot_username_cache is None:
        me = await get_me()
        _bot_username_cache = me.get("username") or ""
    return _bot_username_cache


async def get_sticker_set(name: str) -> Dict[str, Any]:
    return await call("getStickerSet", name=name)


async def get_file(file_id: str) -> Dict[str, Any]:
    return await call("getFile", file_id=file_id)


async def download_file(file_path: str) -> bytes:
    s = await session()
    async with s.get(f"{FILE_BASE}/{file_path}") as resp:
        resp.raise_for_status()
        return await resp.read()


async def download_file_by_id(file_id: str) -> bytes:
    meta = await get_file(file_id)
    return await download_file(meta["file_path"])


async def create_sticker_set(
    user_id: int,
    name: str,
    title: str,
    sticker_bytes: bytes,
    emoji: str,
    sticker_type: str = "custom_emoji",
) -> None:
    s = await session()
    form = aiohttp.FormData()
    form.add_field("user_id", str(user_id))
    form.add_field("name", name)
    form.add_field("title", title)
    form.add_field("sticker_type", sticker_type)
    form.add_field("stickers", _json.dumps([{
        "sticker": "attach://sticker0",
        "format": "animated",
        "emoji_list": [emoji],
    }]))
    form.add_field(
        "sticker0", sticker_bytes,
        filename="sticker.tgs",
        content_type="application/octet-stream",
    )
    async with s.post(f"{BASE}/createNewStickerSet", data=form) as resp:
        data = await resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"createNewStickerSet failed: {data.get('description')}")


async def add_sticker_to_set(
    user_id: int,
    name: str,
    sticker_bytes: bytes,
    emoji: str,
) -> None:
    s = await session()
    form = aiohttp.FormData()
    form.add_field("user_id", str(user_id))
    form.add_field("name", name)
    form.add_field("sticker", _json.dumps({
        "sticker": "attach://sticker_file",
        "format": "animated",
        "emoji_list": [emoji],
    }))
    form.add_field(
        "sticker_file", sticker_bytes,
        filename="sticker.tgs",
        content_type="application/octet-stream",
    )
    async with s.post(f"{BASE}/addStickerToSet", data=form) as resp:
        data = await resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"addStickerToSet failed: {data.get('description')}")


async def set_sticker_set_title(name: str, title: str) -> None:
    await call("setStickerSetTitle", name=name, title=title)