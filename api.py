from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl

from fastapi import FastAPI, File, Header, HTTPException, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import telegram_api as tg
from config import (
    BOT_TOKEN, MAX_ITEMS_PER_PACK, pack_name_suffix, PASSPORT_PACK,
    SKIP_COLOR_PACKS, STARS_PER_EMOJI, SVG_DIR, FONT_DIR, TEMPLATE_SETS,
)
from db import db
from sticker_utils import (
    MAX_ANIMATED_STICKER_BYTES,
    customize_tgs_template,
    customize_tgs_template_with_secondary_text,
    validate_short_name,
    validate_svg_logo,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("api")

app = FastAPI(title="Emoji Mini App API")

INIT_DATA_TTL_SECONDS = 3600
TEMPLATES_CACHE_TTL = 1800
TGS_CACHE_MAX = 256
PREVIEW_CONCURRENCY = 6


# ─────────────────────────────────────────────────────────────────────
# 1. initData validation
# ─────────────────────────────────────────────────────────────────────

def validate_init_data(init_data: str) -> Dict[str, Any]:
    if not init_data:
        raise HTTPException(401, "initData required")
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        raise HTTPException(401, "invalid initData format")

    hash_ = parsed.pop("hash", None)
    if not hash_:
        raise HTTPException(401, "hash missing")

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(
        b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
    ).digest()
    expected_hash = hmac.new(
        secret_key, check_string.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, hash_):
        raise HTTPException(401, "invalid hash")

    try:
        auth_date = int(parsed.get("auth_date", "0"))
    except ValueError:
        raise HTTPException(401, "invalid auth_date")

    if abs(time.time() - auth_date) > INIT_DATA_TTL_SECONDS:
        raise HTTPException(401, "initData expired")

    try:
        user = json.loads(parsed.get("user", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(401, "invalid user payload")

    if not user.get("id"):
        raise HTTPException(401, "user id missing")

    return user


async def auth(x_tg_data: Optional[str]) -> Dict[str, Any]:
    user = validate_init_data(x_tg_data or "")
    await db.ensure_user(
        user["id"],
        user.get("first_name", ""),
        user.get("username"),
    )
    return user


# ─────────────────────────────────────────────────────────────────────
# 2. Templates cache
# ─────────────────────────────────────────────────────────────────────

_templates_cache: Dict[str, Any] = {"ts": 0.0, "data": []}
_templates_lock = asyncio.Lock()
_tgs_cache: Dict[str, bytes] = {}


async def refresh_templates() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pack_key, set_name in TEMPLATE_SETS.items():
        try:
            sset = await tg.get_sticker_set(set_name)
        except Exception as e:
            log.error("Failed to load sticker set %s: %s", set_name, e)
            continue
        for idx, st in enumerate(sset.get("stickers", [])):
            if st.get("is_animated") and st.get("is_video"):
                continue
            out.append({
                "pack": pack_key,
                "index": idx,
                "emoji": st.get("emoji", "⭐"),
                "file_id": st.get("file_id"),
                "url": f"/api/template/{pack_key}/{idx}",
            })
    log.info("Loaded %d templates", len(out))
    return out


async def get_templates() -> List[Dict[str, Any]]:
    if (
        time.time() - _templates_cache["ts"] < TEMPLATES_CACHE_TTL
        and _templates_cache["data"]
    ):
        return _templates_cache["data"]

    async with _templates_lock:
        if (
            time.time() - _templates_cache["ts"] < TEMPLATES_CACHE_TTL
            and _templates_cache["data"]
        ):
            return _templates_cache["data"]
        data = await refresh_templates()
        _templates_cache["data"] = data
        _templates_cache["ts"] = time.time()
        return data


async def find_template(pack_key: str, index: int) -> Optional[Dict[str, Any]]:
    for t in await get_templates():
        if t["pack"] == pack_key and t["index"] == index:
            return t
    return None


async def get_tgs_bytes(file_id: str) -> bytes:
    cached = _tgs_cache.get(file_id)
    if cached is not None:
        return cached
    data = await tg.download_file_by_id(file_id)
    if len(_tgs_cache) > TGS_CACHE_MAX:
        _tgs_cache.clear()
    _tgs_cache[file_id] = data
    return data


# ─────────────────────────────────────────────────────────────────────
# 3. Pydantic
# ─────────────────────────────────────────────────────────────────────

class PreviewPayload(BaseModel):
    pack: str
    index: int
    text: str = ""
    secondary_text: str = ""
    hex_color: Optional[str] = None
    font_id: str = "montserrat"
    font_upload_id: Optional[str] = None
    svg_upload_id: Optional[str] = None
    offset_x: float = 0.0
    offset_y: float = 0.0
    scale: float = 1.0
    rotation: float = 0.0


class GenerateItem(BaseModel):
    pack: str
    index: int
    text: str = ""
    secondary_text: str = ""


class GeneratePayload(PreviewPayload):
    pack_type: str = "emoji"
    pack_title: str = ""
    pack_slug: str = ""
    items: List[GenerateItem] = []


class RenamePayload(BaseModel):
    pack_name: str
    new_title: str


# ─────────────────────────────────────────────────────────────────────
# 4. Helpers
# ─────────────────────────────────────────────────────────────────────

def _resolve_font_path(font_upload_id: Optional[str]) -> Optional[str]:
    if not font_upload_id:
        return None
    for ext in (".ttf", ".otf"):
        p = FONT_DIR / f"{font_upload_id}{ext}"
        if p.exists():
            return str(p)
    return None


def _resolve_svg_bytes(svg_upload_id: Optional[str]) -> Optional[bytes]:
    if not svg_upload_id:
        return None
    p = SVG_DIR / f"{svg_upload_id}.svg"
    if not p.exists():
        return None
    return p.read_bytes()


def _is_passport(pack_key: str) -> bool:
    return pack_key == PASSPORT_PACK


async def _render_one(
    pack_key: str,
    index: int,
    text: str,
    secondary_text: str,
    hex_color: Optional[str],
    font_id: str,
    font_path_override: Optional[str],
    logo_svg: Optional[bytes],
    offset_x: float,
    offset_y: float,
    scale: float,
    rotation: float,
) -> bytes:
    t = await find_template(pack_key, index)
    if not t:
        raise HTTPException(404, f"Template {pack_key}/{index} not found")

    raw = await get_tgs_bytes(t["file_id"])
    skip_color = pack_key in SKIP_COLOR_PACKS
    actual_hex = None if skip_color else (hex_color or "#FFFFFF")

    if _is_passport(pack_key):
        return await asyncio.to_thread(
            customize_tgs_template_with_secondary_text,
            tgs_bytes=raw,
            text=text,
            secondary_text=secondary_text,
            hex_color=actual_hex,
            font_id=font_id,
            logo_svg=logo_svg,
            enforce_size_limit=False,
            skip_color=skip_color,
        )

    return await asyncio.to_thread(
        customize_tgs_template,
        tgs_bytes=raw,
        text=text,
        hex_color=actual_hex,
        font_id=font_id,
        logo_svg=logo_svg,
        enforce_size_limit=False,
        skip_color=skip_color,
        offset_x=offset_x,
        offset_y=offset_y,
        scale=scale,
        rotation=rotation,
        font_path_override=font_path_override,
    )


async def _build_pack_name(slug: str) -> str:
    suffix = pack_name_suffix(await tg.get_bot_username())
    slug = "".join(c for c in slug.lower() if c.isalnum() or c == "_")
    if not slug or not slug[0].isalpha():
        slug = "pack" + slug
    max_base = 64 - len(suffix)
    return slug[:max_base] + suffix


async def _check_name_free(name: str) -> bool:
    try:
        await tg.get_sticker_set(name)
        return False
    except Exception as e:
        msg = str(e).lower()
        if "invalid" in msg or "not found" in msg or "stickerset_invalid" in msg:
            return True
        log.warning("check name %s: %s", name, e)
        return True


# ─────────────────────────────────────────────────────────────────────
# 5. Endpoints
# ─────────────────────────────────────────────────────────────────────

@app.get("/api/me")
async def api_me(x_tg_data: Optional[str] = Header(default=None)):
    user = await auth(x_tg_data)
    u = await db.get_user(user["id"])
    return {
        "id": user["id"],
        "first_name": user.get("first_name"),
        "username": user.get("username"),
        "balance": u.get("balance", 0),
        "stars_per_emoji": STARS_PER_EMOJI,
        "max_items": MAX_ITEMS_PER_PACK,
        "bot_username": await tg.get_bot_username(),
    }


@app.get("/api/templates")
async def api_templates(x_tg_data: Optional[str] = Header(default=None)):
    await auth(x_tg_data)
    data = await get_templates()
    safe = [{k: v for k, v in t.items() if k != "file_id"} for t in data]
    return safe


@app.get("/api/template/{pack_key}/{index}")
async def api_template_file(
    pack_key: str,
    index: int,
    x_tg_data: Optional[str] = Header(default=None),
):
    await auth(x_tg_data)
    t = await find_template(pack_key, index)
    if not t:
        raise HTTPException(404, "template not found")
    data = await get_tgs_bytes(t["file_id"])
    return Response(content=data, media_type="application/gzip")


@app.post("/api/upload-svg")
async def api_upload_svg(
    file: UploadFile = File(...),
    x_tg_data: Optional[str] = Header(default=None),
):
    await auth(x_tg_data)
    if not (file.filename or "").lower().endswith(".svg"):
        raise HTTPException(400, "Only .svg files allowed")
    data = await file.read()
    if len(data) > 256 * 1024:
        raise HTTPException(400, "SVG too big (max 256 KB)")
    try:
        validate_svg_logo(data)
    except Exception as e:
        raise HTTPException(400, f"SVG invalid: {e}")

    uid = uuid.uuid4().hex
    (SVG_DIR / f"{uid}.svg").write_bytes(data)
    return {"svg_id": uid}


@app.post("/api/upload-font")
async def api_upload_font(
    file: UploadFile = File(...),
    x_tg_data: Optional[str] = Header(default=None),
):
    await auth(x_tg_data)
    name = (file.filename or "").lower()
    if not (name.endswith(".ttf") or name.endswith(".otf")):
        raise HTTPException(400, "Only .ttf/.otf allowed")
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "Font too big (max 5 MB)")

    uid = uuid.uuid4().hex
    ext = ".ttf" if name.endswith(".ttf") else ".otf"
    (FONT_DIR / f"{uid}{ext}").write_bytes(data)
    return {"font_id": uid}


@app.post("/api/preview")
async def api_preview(
    payload: PreviewPayload,
    x_tg_data: Optional[str] = Header(default=None),
):
    await auth(x_tg_data)
    font_path = _resolve_font_path(payload.font_upload_id)
    svg_bytes = _resolve_svg_bytes(payload.svg_upload_id)

    try:
        data = await _render_one(
            pack_key=payload.pack,
            index=payload.index,
            text=payload.text,
            secondary_text=payload.secondary_text,
            hex_color=payload.hex_color,
            font_id=payload.font_id,
            font_path_override=font_path,
            logo_svg=svg_bytes,
            offset_x=payload.offset_x,
            offset_y=payload.offset_y,
            scale=payload.scale,
            rotation=payload.rotation,
        )
    except HTTPException:
        raise
    except Exception as e:
        log.exception("preview failed")
        raise HTTPException(500, str(e))

    return Response(content=data, media_type="application/gzip")


@app.get("/api/packs")
async def api_packs(x_tg_data: Optional[str] = Header(default=None)):
    user = await auth(x_tg_data)
    return await db.get_packs(user["id"])


@app.post("/api/packs/rename")
async def api_packs_rename(
    payload: RenamePayload,
    x_tg_data: Optional[str] = Header(default=None),
):
    user = await auth(x_tg_data)
    new_title = (payload.new_title or "").strip()[:64]
    if not new_title:
        raise HTTPException(400, "Empty title")

    ok = await db.rename_pack(user["id"], payload.pack_name, new_title)
    if not ok:
        raise HTTPException(404, "pack not found")

    try:
        await tg.set_sticker_set_title(payload.pack_name, new_title)
    except Exception as e:
        log.warning("setStickerSetTitle failed: %s", e)

    return {"ok": True}


@app.post("/api/generate")
async def api_generate(
    payload: GeneratePayload,
    x_tg_data: Optional[str] = Header(default=None),
):
    user = await auth(x_tg_data)
    uid = user["id"]

    # 1. Собираем айтемы
    items: List[Dict[str, Any]] = []
    if payload.items:
        for it in payload.items:
            items.append({
                "pack": it.pack,
                "index": it.index,
                "text": it.text or payload.text,
                "secondary_text": it.secondary_text or payload.secondary_text,
            })
    else:
        items.append({
            "pack": payload.pack,
            "index": payload.index,
            "text": payload.text,
            "secondary_text": payload.secondary_text,
        })

    if not items:
        raise HTTPException(400, "No items to generate")
    if len(items) > MAX_ITEMS_PER_PACK:
        raise HTTPException(400, f"Too many items (max {MAX_ITEMS_PER_PACK})")

    # 2. Проверяем баланс
    price = len(items) * STARS_PER_EMOJI
    u = await db.get_user(uid)
    balance = float(u.get("balance", 0))
    if balance + 1e-9 < price:
        raise HTTPException(
            402,
            f"Недостаточно средств. Нужно {price} ⭐, у тебя {balance:.0f} ⭐",
        )

    if not await db.try_spend(uid, float(price)):
        raise HTTPException(402, "Не удалось списать средства")

    try:
        # 3. Проверяем имя пака
        slug = payload.pack_slug or payload.pack_title or "pack"
        pack_name = await _build_pack_name(slug)
        if not validate_short_name(pack_name):
            raise HTTPException(400, "Некорректный slug")
        if not await _check_name_free(pack_name):
            raise HTTPException(409, f"Имя пака занято: {pack_name}")

        # 4. Рендерим
        font_path = _resolve_font_path(payload.font_upload_id)
        svg_bytes = _resolve_svg_bytes(payload.svg_upload_id)

        sem = asyncio.Semaphore(PREVIEW_CONCURRENCY)

        async def render(item: Dict[str, Any]):
            async with sem:
                return await _render_one(
                    pack_key=item["pack"],
                    index=item["index"],
                    text=item["text"],
                    secondary_text=item["secondary_text"],
                    hex_color=payload.hex_color,
                    font_id=payload.font_id,
                    font_path_override=font_path,
                    logo_svg=svg_bytes,
                    offset_x=payload.offset_x,
                    offset_y=payload.offset_y,
                    scale=payload.scale,
                    rotation=payload.rotation,
                )

        rendered = await asyncio.gather(
            *(render(it) for it in items), return_exceptions=True
        )

        ok_items: List[Dict[str, Any]] = []
        for it, r in zip(items, rendered):
            if isinstance(r, Exception):
                log.error("render %s/%s failed: %s", it["pack"], it["index"], r)
                continue
            if len(r) > MAX_ANIMATED_STICKER_BYTES:
                log.error("item too big: %d > %d", len(r), MAX_ANIMATED_STICKER_BYTES)
                continue
            ok_items.append({"bytes": r, "item": it})

        if not ok_items:
            await db.refund(uid, float(price))
            raise HTTPException(500, "Не удалось сгенерировать ни одного стикера")

        # 5. Создаём пак
        sticker_type = "custom_emoji" if payload.pack_type == "emoji" else "regular"
        title = (payload.pack_title or payload.text or "Pack").strip()[:64] or "Pack"

        templates = await get_templates()
        emoji_map = {
            (t["pack"], t["index"]): t.get("emoji", "⭐") for t in templates
        }

        first = ok_items[0]
        first_emoji = emoji_map.get(
            (first["item"]["pack"], first["item"]["index"]), "⭐"
        )

        await tg.create_sticker_set(
            user_id=uid,
            name=pack_name,
            title=title,
            sticker_bytes=first["bytes"],
            emoji=first_emoji,
            sticker_type=sticker_type,
        )

        for entry in ok_items[1:]:
            emoji = emoji_map.get(
                (entry["item"]["pack"], entry["item"]["index"]), "⭐"
            )
            await tg.add_sticker_to_set(uid, pack_name, entry["bytes"], emoji)
            await asyncio.sleep(0.05)

        link_base = "addemoji" if payload.pack_type == "emoji" else "addstickers"
        link = f"https://t.me/{link_base}/{pack_name}"
        await db.add_pack(uid, pack_name, title, payload.pack_type, link)

        return {
            "ok": True,
            "link": link,
            "pack_name": pack_name,
            "count": len(ok_items),
        }

    except HTTPException:
        await db.refund(uid, float(price))
        raise
    except Exception as e:
        log.exception("generate failed")
        await db.refund(uid, float(price))
        raise HTTPException(500, str(e))


# ─────────────────────────────────────────────────────────────────────
# 6. Startup / shutdown
# ─────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup():
    try:
        username = await tg.get_bot_username()
        log.info("Bot username resolved: @%s", username)
    except Exception as e:
        log.error("Failed to fetch bot username on startup: %s", e)


@app.on_event("shutdown")
async def _shutdown():
    await tg.close()


STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")