from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ─── Из .env ─────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
CRYPTO_PAY_TOKEN = os.getenv("CRYPTO_PAY_TOKEN", "")

# ─── Статичные константы ─────────────────────────────────────────────
ADMIN_ID = 7752488661

STARS_PER_EMOJI = 1
RUB_PER_EMOJI = 2
REFERRAL_BONUS_STARS = 3
REFERRAL_PERCENT = 10
MAX_TOPUP_STARS = 10000
MAX_TOPUP_RUB = 50000
MAX_ITEMS_PER_PACK = 50

PACK_NAME_SUFFIX = "_by_animatedemojimakerbot"

SUPPORT_USERNAME = "ecronox"
CHANNEL_USERNAME = "AnimatedEmojiMaker"
CHANNEL_ID = -1004414125985
CHANNEL_URL = f"https://t.me/{CHANNEL_USERNAME}"

CRYPTO_PAY_API_URL = "https://pay.crypt.bot/api"

# ─── Наборы-шаблоны ──────────────────────────────────────────────────
TEMPLATE_SETS = {
    "color":      "color_by_animatedemojimakerbot",
    "exclusive":  "exclusive_by_animatedemojimakerbot",
    "pepe":       "pepe_by_animatedemojimakerbot",
    "passport":   "passport_by_animatedemojimakerbot",
    "black_hole": "blackhole_by_animatedemojimakerbot",
}

SKIP_COLOR_PACKS = {"color", "exclusive", "pepe"}
PASSPORT_PACK = "passport"

# ─── WebApp URL ──────────────────────────────────────────────────────
WEBAPP_URL = "https://animatedemojimaker.bothost.tech"

# ─── Пути ────────────────────────────────────────────────────────────
DATA_DIR = Path("data")
UPLOAD_DIR = DATA_DIR / "uploads"
SVG_DIR = UPLOAD_DIR / "svg"
FONT_DIR = UPLOAD_DIR / "fonts"
for d in (DATA_DIR, UPLOAD_DIR, SVG_DIR, FONT_DIR):
    d.mkdir(parents=True, exist_ok=True)

DB_PATH = str(DATA_DIR / "database.db")