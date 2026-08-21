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

PRICE_RE = re.compile(r"(-\s*)(\d{3,7})")

# Наценка, которую УЖЕ применил посредник (DMGadgets/marselUAE) поверх
# настоящей закупочной цены — нужно вычесть её, прежде чем накидывать свою.
# Формулы взяты из кода, который прислал сам посредник.
SUPPLIER_MARKUP_KEYWORDS = [
    ("ray-ban", "flat3000"),
    ("meta glasses", "flat3000"),
    ("очки", "flat3000"),
    ("macbook", "flat4000"),
    ("фототехника", "flat4000"),
    ("gopro", "flat4000"),
    ("instax", "flat4000"),
    ("canon", "flat4000"),
    ("ipad", "flat3000"),
    ("gaming", "flat3000"),
    ("dyson", "flat5000"),
    ("аксессуары apple", "flat2000"),
    ("pitaka", "flat2000"),
    ("зарядк", "flat1000"),
    ("charger", "flat1000"),
    ("airpods", "low10k"),
    ("galaxy buds", "low10k"),
    ("buds", "low10k"),
    ("акустика", "low10k"),
    ("яндекс", "low10k"),
    ("acoustics", "low10k"),
]
DEFAULT_SUPPLIER_BUCKET = "default50k"


def classify_supplier_bucket(header_line_lower: str) -> str | None:
    for keyword, bucket in SUPPLIER_MARKUP_KEYWORDS:
        if keyword in header_line_lower:
            return bucket
    return None


def reverse_supplier_margin(bucket: str, displayed_price: int) -> int:
    if bucket == "low10k":
        return displayed_price - (2000 if displayed_price >= 12000 else 1000)
    if bucket == "flat4000":
        return displayed_price - 4000
    if bucket == "flat3000":
        return displayed_price - 3000
    if bucket == "flat5000":
        return displayed_price - 5000
    if bucket == "flat2000":
        return displayed_price - 2000
    if bucket == "flat1000":
        return displayed_price - 1000
    # default50k
    return displayed_price - (3000 if displayed_price >= 53000 else 2000)


def get_margin(old_price: int) -> int:
    return MARGIN_LOW if old_price < MARGIN_THRESHOLD else MARGIN_HIGH


def apply_margin(text: str) -> str:
    """Вычитает наценку посредника (по категории) и накидывает свою."""
    current_bucket = DEFAULT_SUPPLIER_BUCKET
    out_lines = []

    for line in text.split("\n"):
        if "•••" in line:
            stripped = line.strip()
            if not stripped.startswith("🔘"):
                # заголовок категории/подкатегории — переопределяем формулу
                matched = classify_supplier_bucket(line.lower())
                current_bucket = matched or DEFAULT_SUPPLIER_BUCKET
            # "🔘 ..." — просто разделитель модели внутри той же категории,
            # формулу не меняем
            out_lines.append(line)
            continue

        def repl(m, bucket=current_bucket):
            displayed = int(m.group(2))
            base = reverse_supplier_margin(bucket, displayed)
            final = base + get_margin(base)
            return f"{m.group(1)}{final}"

        out_lines.append(PRICE_RE.sub(repl, line))

    return "\n".join(out_lines)


def normalize_key(header: str) -> str:
    return re.sub(r"•+", "", header).strip().lower()


def group_key(text: str) -> str:
    for line in text.split("\n"):
        if "•••" in line:
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
