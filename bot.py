import asyncio
import json
import logging
import os
import re
import shutil

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
TARGET_CHANNEL = os.environ.get("TARGET_CHANNEL", "@mature_resident")
MAP_FILE = os.environ.get(
    "MAP_FILE", os.path.join(os.path.dirname(__file__), "category_map.json")
)
SEED_FILE = os.path.join(os.path.dirname(__file__), "category_map.seed.json")

MARGIN_THRESHOLD = 50000
MARGIN_LOW = 1000
MARGIN_HIGH = 2000

HEADER_RE = re.compile(r"^[^\wА-Яа-я\n]*[📱🎧⌚️🤖💻🎮👓🌪️🔌🔘📋🎵].+?•{3,}")
PRICE_RE = re.compile(r"(-\s*)(\d{3,7})")


def get_margin(old_price: int) -> int:
    return MARGIN_LOW if old_price < MARGIN_THRESHOLD else MARGIN_HIGH


def apply_margin(text: str) -> str:
    def repl(m):
        price = int(m.group(2))
        return f"{m.group(1)}{price + get_margin(price)}"

    return PRICE_RE.sub(repl, text)


def normalize_key(header: str) -> str:
    return re.sub(r"•+", "", header).strip().lower()


def group_key(text: str) -> str:
    for line in text.split("\n"):
        if HEADER_RE.match(line):
            return normalize_key(line)
    return normalize_key(text.split("\n")[0])


def load_map() -> dict:
    if not os.path.exists(MAP_FILE) and os.path.exists(SEED_FILE):
        os.makedirs(os.path.dirname(MAP_FILE), exist_ok=True)
        shutil.copy(SEED_FILE, MAP_FILE)
    try:
        with open(MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_map(m: dict) -> None:
    os.makedirs(os.path.dirname(MAP_FILE), exist_ok=True)
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)


bot = Bot(BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.reply(
        "Пришли (или перешли) пост с прайсом из канала поставщика — "
        "я пересчитаю цены с наценкой (до 50к +1000, от 50к +2000) и "
        "отредактирую соответствующий пост в канале. Новые посты не публикую — "
        "если категория ещё не привязана, использую /remap."
    )


@dp.message(Command("map"))
async def cmd_map(message: Message):
    cat_map = load_map()
    if not cat_map:
        await message.reply("Пока пусто.")
        return
    lines = [f"{k[:40]} -> msg {v}" for k, v in cat_map.items()]
    await message.reply("\n".join(lines))


@dp.message(Command("remap"))
async def cmd_remap(message: Message):
    # /remap <ключ через _ вместо пробелов> <message_id>
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await message.reply("Использование: /remap <ключ> <message_id>")
        return
    _, key, msg_id = parts
    cat_map = load_map()
    cat_map[key.replace("_", " ").strip().lower()] = int(msg_id)
    save_map(cat_map)
    await message.reply("Ок, привязка обновлена.")


@dp.message(F.text | F.caption)
async def handle_forward(message: Message):
    text = message.text or message.caption
    if not text or text.startswith("/"):
        return

    new_text = apply_margin(text)
    key = group_key(text)
    cat_map = load_map()
    target_id = cat_map.get(key)

    if not target_id:
        await message.reply(
            f"⚠️ Нет привязки для «{key[:60]}» — ничего не публикую.\n"
            f"Привяжите вручную: /remap {key.replace(' ', '_')} <message_id>"
        )
        return

    try:
        await bot.edit_message_text(
            chat_id=TARGET_CHANNEL, message_id=target_id, text=new_text
        )
        await message.reply(f"✏️ обновлено: {key[:60]}")
        return
    except TelegramBadRequest as e:
        if "there is no text in the message to edit" not in str(e):
            logging.warning("edit_text failed for %s: %s", key, e)
            await message.reply(f"⚠️ Не удалось отредактировать пост (id {target_id}): {e}")
            return

    # Пост с фото — цены хранятся в подписи (caption), а не в тексте
    try:
        await bot.edit_message_caption(
            chat_id=TARGET_CHANNEL, message_id=target_id, caption=new_text
        )
        await message.reply(f"✏️ обновлено (подпись к фото): {key[:60]}")
    except TelegramBadRequest as e:
        logging.warning("edit_caption failed for %s: %s", key, e)
        await message.reply(f"⚠️ Не удалось отредактировать пост (id {target_id}): {e}")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
