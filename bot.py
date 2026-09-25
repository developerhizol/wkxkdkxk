from __future__ import annotations
import asyncio
import logging
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    Message, InlineKeyboardButton, InlineKeyboardMarkup,
    LabeledPrice, PreCheckoutQuery, WebAppInfo,
)

from config import BOT_TOKEN, WEBAPP_URL, STARS_PER_EMOJI
from db import db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("bot")

router = Router()


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🚀 Открыть конструктор",
            web_app=WebAppInfo(url=WEBAPP_URL),
        )],
        [InlineKeyboardButton(text="💰 Пополнить баланс", callback_data="topup:menu")],
    ])


async def show_topup_menu(message: Message) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="50 ⭐", callback_data="topup:50"),
         InlineKeyboardButton(text="100 ⭐", callback_data="topup:100")],
        [InlineKeyboardButton(text="250 ⭐", callback_data="topup:250"),
         InlineKeyboardButton(text="500 ⭐", callback_data="topup:500")],
        [InlineKeyboardButton(text="🚀 В конструктор",
                              web_app=WebAppInfo(url=WEBAPP_URL))],
    ])
    await message.answer(
        f"💰 <b>Пополнение</b>\n\n1 генерация = {STARS_PER_EMOJI} ⭐\n\nВыбери сумму:",
        reply_markup=kb,
        parse_mode=ParseMode.HTML,
    )


@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject) -> None:
    await db.ensure_user(
        message.from_user.id,
        message.from_user.first_name or "",
        message.from_user.username,
    )
    args = (command.args or "").strip()
    if args.startswith("topup"):
        await show_topup_menu(message)
        return
    user = await db.get_user(message.from_user.id)
    await message.answer(
        f"👋 Привет, <b>{message.from_user.first_name}</b>!\n\n"
        f"💰 Баланс: <b>{user.get('balance', 0):.0f} ⭐</b>\n\n"
        f"Открой конструктор — выбери шаблоны, настрой текст и цвет, "
        f"посмотри превью и создай пак.",
        reply_markup=main_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("buy"))
async def cmd_buy(message: Message) -> None:
    await show_topup_menu(message)


@router.callback_query(F.data == "topup:menu")
async def cb_topup_menu(call, bot: Bot) -> None:
    await call.answer()
    await show_topup_menu(call.message)


@router.callback_query(F.data.startswith("topup:"))
async def cb_topup(call, bot: Bot) -> None:
    part = call.data.split(":", 1)[1]
    if part == "menu":
        await call.answer()
        await show_topup_menu(call.message)
        return
    try:
        stars = int(part)
    except ValueError:
        await call.answer("Ошибка суммы", show_alert=True)
        return
    await bot.send_invoice(
        chat_id=call.from_user.id,
        title=f"Пополнение на {stars} ⭐",
        description=f"Зачисление {stars} ⭐ на баланс бота.",
        payload=f"topup:{stars}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{stars} ⭐", amount=stars)],
    )
    await call.answer()


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, bot: Bot) -> None:
    await bot.answer_pre_checkout_query(q.id, ok=True)


@router.message(F.successful_payment)
async def on_payment(message: Message) -> None:
    payload = message.successful_payment.invoice_payload
    try:
        _, amount = payload.split(":")
        stars = int(amount)
    except Exception:
        return
    await db.ensure_user(
        message.from_user.id,
        message.from_user.first_name or "",
        message.from_user.username,
    )
    await db.add_balance(message.from_user.id, float(stars))
    await message.answer(
        f"✅ Баланс пополнен на <b>{stars} ⭐</b>\n\n"
        f"Вернись в конструктор и создай пак 👇",
        reply_markup=main_kb(),
        parse_mode=ParseMode.HTML,
    )


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    log.info("Bot polling started…")
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "pre_checkout_query"],
    )


if __name__ == "__main__":
    asyncio.run(main())