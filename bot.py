import os
import io
import json
import logging
import asyncio
import random
from pathlib import Path
from datetime import datetime

import anthropic
import fal_client
import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

CLAUDE_API_KEY        = os.environ["CLAUDE_API_KEY"]
TELEGRAM_BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
FAL_KEY               = os.environ["FAL_KEY"]
TELEGRAM_USER_ID      = int(os.environ["TELEGRAM_USER_ID"])
SECOND_APPROVER_ID    = 182606553
INSTAGRAM_USERNAME = os.environ.get("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.environ.get("INSTAGRAM_PASSWORD", "")

BRAND_RED = "#A70D19"
IMAGE_W, IMAGE_H = 1080, 1920
OUTPUT_DIR = Path("approved_stories")
OUTPUT_DIR.mkdir(exist_ok=True)

IIKO_FEATURES = [
    ("SmartControl", "real-time phone control"),
    ("Moliyaviy hisobotlar", "financial reports online"),
    ("Ombor boshqaruvi", "warehouse management"),
    ("Xodimlarni boshqarish", "staff management"),
    ("Mehmonlar sodiqlik tizimi", "loyalty system iikoCard"),
    ("Yetkazib berish", "iikoDelivery"),
    ("Kassa iikoFront", "POS system"),
    ("Tarmoq boshqaruvi", "iikoChain network"),
    ("Tashqi menyu", "iikoWeb online menu"),
    ("Hisobotlar 2.0", "advanced analytics"),
    ("Koll-markaz", "call center"),
    ("API integratsiya", "third party integrations"),
    ("Kassa nazorati", "cash control security"),
    ("Taom tannarxi", "food cost calculation"),
    ("Franshiza boshqaruvi", "franchise management"),
]

claude_client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)

_story_slots: dict[int, dict] = {}

_FONT_BOLD    = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pick_features(n: int = 5) -> list[tuple[str, str]]:
    return random.sample(IIKO_FEATURES, n)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _find_font(bold: bool = False, size: int = 40) -> ImageFont.FreeTypeFont:
    _here = os.path.dirname(os.path.abspath(__file__))
    bundled_bold    = os.path.join(_here, "fonts", "DejaVuSans-Bold.ttf")
    bundled_regular = os.path.join(_here, "fonts", "DejaVuSans.ttf")

    candidates = (
        [
            bundled_bold,
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ] if bold else [
            bundled_regular,
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
    )
    for candidate in candidates:
        if os.path.exists(candidate):
            try:
                font = ImageFont.truetype(candidate, size)
                logger.info("Font loaded: %s @ %dpx", candidate, size)
                return font
            except Exception:
                continue

    logger.error("No TrueType font found — bundled fonts missing? Check fonts/ directory.")
    return ImageFont.load_default()


def _fit_font_size(text: str, draw: ImageDraw.ImageDraw, max_width: int,
                   sizes: tuple, bold: bool = True) -> int:
    """Return the largest size from `sizes` whose rendered text fits max_width."""
    for size in sizes:
        font = _find_font(bold=bold, size=size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_width:
            return size
    return sizes[-1]


def _primary_keyboard(slot_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Tasdiqlash",        callback_data=f"approve:{slot_idx}"),
            InlineKeyboardButton("🔄 Qayta yaratish",   callback_data=f"regen:{slot_idx}"),
            InlineKeyboardButton("❌ O'tkazib yuborish", callback_data=f"skip:{slot_idx}"),
        ],
        [
            InlineKeyboardButton("✏️ Tahrirlash",       callback_data=f"edit:{slot_idx}"),
        ],
    ])


def _final_approval_keyboard(slot_idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Tasdiqlash",  callback_data=f"confirm_final:{slot_idx}"),
            InlineKeyboardButton("❌ Rad etish",   callback_data=f"reject_final:{slot_idx}"),
        ],
    ])


def _story_caption(slot_num: int, story: dict, suffix: str = "") -> str:
    return (
        f"📸 *Story #{slot_num}*{suffix}\n\n"
        f"🏷 *Xususiyat:* {story['feature_name']}\n"
        f"📝 *Sarlavha:* {story['title']}\n"
        f"💬 *Taglavha:* {story['subtitle']}"
    )


# ---------------------------------------------------------------------------
# Image composition
# ---------------------------------------------------------------------------

def _wrap_title(title: str, max_chars: int = 18) -> list[str]:
    """Wrap title into at most 2 lines at word boundaries."""
    words = title.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = (current + " " + word).strip() if current else word
        if len(test) <= max_chars:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) == 1 and current:
            # Already have first line — collect rest into second line
            rest = current + " " + " ".join(
                words[words.index(word) + 1:]
            ) if word != words[-1] else current
            lines.append(rest.strip())
            break
    else:
        if current:
            lines.append(current)
    return lines[:2]


def compose_story_image(photo_bytes: bytes, title: str, subtitle: str = "") -> bytes:
    # ── 1. Base photo — fill entire 1080×1920 canvas ─────────────────────────
    photo = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    photo = photo.resize((IMAGE_W, IMAGE_H), Image.LANCZOS)

    # ── 2. Red gradient overlay, line-by-line alpha ───────────────────────────
    #   Formula: alpha(y) = int(255 * max(0, 1 - y/730))
    #   y=0   → 255 (fully opaque),  y=300 → 150,  y=500 → 45,  y=730 → 0
    GRAD_H   = 730
    y_idx    = np.arange(IMAGE_H, dtype=np.float32)
    alpha_1d = np.clip(255.0 * (1.0 - y_idx / GRAD_H), 0, 255).astype(np.uint8)

    overlay_arr = np.zeros((IMAGE_H, IMAGE_W, 4), dtype=np.uint8)
    overlay_arr[:, :, 0] = 167   # red
    overlay_arr[:, :, 1] = 13    # green
    overlay_arr[:, :, 2] = 25    # blue
    overlay_arr[:, :, 3] = alpha_1d[:, np.newaxis]   # per-row alpha

    overlay = Image.fromarray(overlay_arr, mode="RGBA")
    canvas  = Image.alpha_composite(photo.convert("RGBA"), overlay)
    draw    = ImageDraw.Draw(canvas)

    def _draw_centered(d, text, font, y):
        """Draw text centered horizontally with drop-shadow."""
        bbox = d.textbbox((0, 0), text, font=font)
        w    = bbox[2] - bbox[0]
        x    = (IMAGE_W - w) // 2
        # shadow
        d.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, 160))
        # white text
        d.text((x, y), text, font=font, fill=(255, 255, 255, 255))
        return bbox[3] - bbox[1]   # return rendered line height

    def _draw_left(d, text, font, x, y, shadow_offset=4):
        """Draw text left-aligned with drop shadow."""
        d.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=(0, 0, 0, 160))
        d.text((x, y), text, font=font, fill=(255, 255, 255, 255))

    # ── 3. Logo "Z E T T A" — centered, y=100, 68px regular ─────────────────
    logo_size = 68
    logo_font = _find_font(bold=False, size=logo_size)
    logger.info("Logo font size: %dpx", logo_size)
    _draw_centered(draw, "Z E T T A", logo_font, y=100)

    # ── 4. Title — BOLD, single line, auto-fit, left x=60, y=200 ─────────────
    title_upper = title.upper()
    max_title_w = IMAGE_W - 60 - 40  # 60px left margin, 40px right padding
    t_size = 130
    for candidate_size in (130, 110, 90, 70, 55):
        test_font = _find_font(bold=True, size=candidate_size)
        bbox = draw.textbbox((0, 0), title_upper, font=test_font)
        if (bbox[2] - bbox[0]) <= max_title_w:
            t_size = candidate_size
            break
    title_font = _find_font(bold=True, size=t_size)
    logger.info("Title font size: %dpx (title len=%d)", t_size, len(title_upper))
    _draw_left(draw, title_upper, title_font, x=60, y=200, shadow_offset=4)

    # ── 6. Output — exactly 1080×1920 JPEG ───────────────────────────────────
    result = canvas.convert("RGB")
    assert result.size == (IMAGE_W, IMAGE_H), f"Bad size: {result.size}"
    buf = io.BytesIO()
    result.save(buf, format="JPEG", quality=95)
    buf.seek(0)
    return buf.read()


# ---------------------------------------------------------------------------
# Claude content generation
# ---------------------------------------------------------------------------

def generate_story_content(feature_name: str, feature_desc: str) -> dict:
    logger.info("Generating content for feature: %s", feature_name)
    prompt = f"""Sen Zetta Group uchun Instagram Stories kontent yaratuvchisan.
Zetta Group — O'zbekistondagi iiko rasmiy hamkori. Restoran biznesini avtomatlashtirish yechimlari.

Quyidagi iiko xususiyati uchun kontent yarat:
- Xususiyat: {feature_name} ({feature_desc})

Faqat JSON qaytargin, hech qanday izoh yo'q:
{{
  "feature_name": "{feature_name}",
  "title": "KATTA HARFLARDA, MAKSIMAL 4 SO'Z, BITTA QATORDA SIG'ADIGAN SLOGAN",
  "image_prompt": "Describe a bright, upscale restaurant interior scene related to {feature_name}. Include: elegantly dressed staff (waiter or manager) smiling and using a smartphone or tablet, happy guests at white-tablecloth tables with flowers and wine glasses, large windows with natural daylight flooding the room, warm neutral tones (cream, beige, soft wood), shallow depth of field, professional editorial photography style. The scene must feel premium, modern, and welcoming. No text, no logos, no watermarks in image."
}}

Muhim: title o'zbek tilida bo'lsin, JUDA qisqa (maksimal 4 so'z) — rasmda BITTA QATORDA joylashishi kerak. Subtitle kerak emas. image_prompt inglizcha va batafsil bo'lsin."""

    response = claude_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw  = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    data = json.loads(raw)
    logger.info("Content generated: %s", data.get("title"))
    return data


def generate_edited_content(story: dict, edit_request: str) -> dict:
    logger.info("Editing story with request: %s", edit_request)
    prompt = f"""Quyidagi Instagram Story kontentini foydalanuvchi so'roviga ko'ra tahrirlash kerak.

Mavjud kontent:
- feature_name: {story['feature_name']}
- title: {story['title']}
- subtitle: {story['subtitle']}
- image_prompt: {story['image_prompt']}

Foydalanuvchi so'rovi: {edit_request}

Faqat o'zgartirilishi kerak bo'lgan maydonlarni yangilang. O'zgartirilmagan maydonlarni aynan saqlang.
Faqat JSON qaytargin, hech qanday izoh yo'q:
{{
  "feature_name": "...",
  "title": "KATTA HARFLARDA, MAKSIMAL 5 SO'Z",
  "subtitle": "Maksimal 10 so'z",
  "image_prompt": "Detailed English prompt..."
}}"""

    response = claude_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw  = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    data = json.loads(raw)
    logger.info("Edited content: %s", data.get("title"))
    return data


# ---------------------------------------------------------------------------
# fal.ai image generation — flux-pro for photorealistic results
# ---------------------------------------------------------------------------

async def generate_fal_image(image_prompt: str) -> bytes:
    logger.info("Generating image via fal.ai flux-pro...")
    enhanced = (
        f"{image_prompt}. "
        "Style: bright upscale restaurant interior, natural daylight from large windows, "
        "warm neutral tones (cream, beige, soft wood), elegantly dressed staff smiling, "
        "happy guests at white-tablecloth tables with flowers and wine glasses, "
        "shallow depth of field, professional editorial photography, "
        "high-end magazine quality, 4K, photorealistic, "
        "no text, no logos, no watermarks, no UI elements"
    )

    def _run():
        return fal_client.run(
            "fal-ai/flux-pro",
            arguments={
                "prompt": enhanced,
                "image_size": {"width": IMAGE_W, "height": IMAGE_H},
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
                "num_images": 1,
                "enable_safety_checker": True,
            },
        )

    loop   = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run)

    image_url = result["images"][0]["url"]
    logger.info("Image URL received, downloading...")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(image_url)
        resp.raise_for_status()
        return resp.content


# ---------------------------------------------------------------------------
# Instagram publishing via instagrapi
# ---------------------------------------------------------------------------

def _instagram_upload_sync(image_bytes: bytes, slot_num: int) -> bool:
    import tempfile
    from instagrapi import Client

    if not INSTAGRAM_USERNAME or not INSTAGRAM_PASSWORD:
        logger.warning("Instagram credentials not set — skipping publish")
        return False

    logger.info("Logging in to Instagram as %s...", INSTAGRAM_USERNAME)
    cl = Client()
    cl.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
    logger.info("Instagram login successful")

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name

    try:
        cl.story_upload_photo(tmp_path, caption="")
        logger.info("Story #%d published to Instagram successfully", slot_num)
        return True
    finally:
        os.unlink(tmp_path)


async def publish_to_instagram(image_bytes: bytes, slot_num: int) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _instagram_upload_sync, image_bytes, slot_num)


# ---------------------------------------------------------------------------
# Build / send story
# ---------------------------------------------------------------------------

async def build_story(feature_name: str, feature_desc: str) -> dict:
    loop    = asyncio.get_event_loop()
    content = await loop.run_in_executor(None, generate_story_content, feature_name, feature_desc)

    photo_bytes = await generate_fal_image(content["image_prompt"])
    composed    = compose_story_image(photo_bytes, content["title"], content.get("subtitle", ""))

    return {
        "feature_name": feature_name,
        "title":        content["title"],
        "subtitle":     content["subtitle"],
        "image_prompt": content["image_prompt"],
        "image_bytes":  composed,
    }


async def build_edited_story(story: dict, edit_request: str) -> dict:
    loop    = asyncio.get_event_loop()
    content = await loop.run_in_executor(None, generate_edited_content, story, edit_request)

    photo_bytes = await generate_fal_image(content["image_prompt"])
    composed    = compose_story_image(photo_bytes, content["title"], content.get("subtitle", ""))

    return {
        "feature_name": content["feature_name"],
        "title":        content["title"],
        "subtitle":     content["subtitle"],
        "image_prompt": content["image_prompt"],
        "image_bytes":  composed,
    }


async def send_story_for_approval(bot, slot_idx: int, story: dict, suffix: str = ""):
    slot_num  = slot_idx + 1
    photo_buf = io.BytesIO(story["image_bytes"])
    photo_buf.name = f"story_{slot_num}.jpg"
    photo_buf.seek(0)

    await bot.send_photo(
        chat_id=TELEGRAM_USER_ID,
        photo=photo_buf,
        caption=_story_caption(slot_num, story, suffix),
        parse_mode="Markdown",
        reply_markup=_primary_keyboard(slot_idx),
    )
    logger.info("Story #%d sent for approval%s", slot_num, suffix)


# ---------------------------------------------------------------------------
# Daily generation
# ---------------------------------------------------------------------------

async def run_daily_generation(app):
    logger.info("Starting daily story generation...")
    await app.bot.send_message(
        chat_id=TELEGRAM_USER_ID,
        text="🚀 *Bugungi 5 ta Instagram Story yaratilmoqda...*\nBiroz kuting.",
        parse_mode="Markdown",
    )

    features = _pick_features(5)
    _story_slots.clear()

    for slot_idx, (feat_name, feat_desc) in enumerate(features):
        try:
            story = await build_story(feat_name, feat_desc)
            _story_slots[slot_idx] = story
            await send_story_for_approval(app.bot, slot_idx, story)
            await asyncio.sleep(1)
        except Exception as e:
            logger.error("Error building story #%d: %s", slot_idx + 1, e)
            await app.bot.send_message(
                chat_id=TELEGRAM_USER_ID,
                text=f"⚠️ Story #{slot_idx + 1} yaratishda xatolik: {e}",
            )


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, slot_str = query.data.split(":", 1)
    slot_idx = int(slot_str)
    slot_num = slot_idx + 1

    # --- APPROVE (step 1) — forward to second approver ---
    if action == "approve":
        story = _story_slots.get(slot_idx)
        if not story:
            await query.edit_message_caption(caption=f"⚠️ Story #{slot_num} topilmadi.")
            return

        await query.edit_message_caption(
            caption=f"⏳ *Story #{slot_num} ikkinchi tasdiqlash uchun yuborildi...*",
            parse_mode="Markdown",
        )

        photo_buf = io.BytesIO(story["image_bytes"])
        photo_buf.name = f"story_{slot_num}_final.jpg"
        photo_buf.seek(0)

        msg = await context.bot.send_photo(
            chat_id=SECOND_APPROVER_ID,
            photo=photo_buf,
            caption=(
                f"📋 *Zetta Stories — Tasdiqlash so'rovi*\n\n"
                f"📸 Story #{slot_num}\n"
                f"🏷 *Xususiyat:* {story['feature_name']}\n"
                f"📝 *Sarlavha:* {story['title']}\n"
                f"💬 *Taglavha:* {story['subtitle']}\n\n"
                "Instagram Stories-ga joylashtirilsinmi?"
            ),
            parse_mode="Markdown",
            reply_markup=_final_approval_keyboard(slot_idx),
        )

        # Save Telegram file_id so we can get a public URL for Instagram
        _story_slots[slot_idx]["telegram_file_id"] = msg.photo[-1].file_id
        logger.info("Story #%d forwarded to second approver (ID %d)", slot_num, SECOND_APPROVER_ID)

    # --- FINAL CONFIRM (second approver approved) — publish to Instagram ---
    elif action == "confirm_final":
        story = _story_slots.get(slot_idx)
        if not story:
            await query.edit_message_caption(caption=f"⚠️ Story #{slot_num} topilmadi.")
            return

        await query.edit_message_caption(
            caption=f"✅ *Story #{slot_num} tasdiqlandi!* Instagram-ga yuklanmoqda...",
            parse_mode="Markdown",
        )

        # Save locally
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = OUTPUT_DIR / f"story_{slot_num}_{timestamp}.jpg"
        filename.write_bytes(story["image_bytes"])
        logger.info("Story #%d saved → %s", slot_num, filename)

        # Publish to Instagram using stored image bytes
        instagram_ok = False
        try:
            instagram_ok = await publish_to_instagram(story["image_bytes"], slot_num)
        except Exception as e:
            logger.error("Instagram publish error for story #%d: %s", slot_num, e)

        ig_status = "✅ Instagram-ga joylashtirildi!" if instagram_ok else "⚠️ Instagram-ga joylashtirishda xatolik yoki sozlamalar yo'q."
        await query.edit_message_caption(
            caption=(
                f"✅ *Story #{slot_num} tasdiqlandi va saqlandi!*\n"
                f"{ig_status}\n"
                f"📁 `{filename.name}`"
            ),
            parse_mode="Markdown",
        )

        # Notify the primary user
        await context.bot.send_message(
            chat_id=TELEGRAM_USER_ID,
            text=(
                f"✅ *Story #{slot_num} ikkinchi shaxs tomonidan tasdiqlandi!*\n"
                f"{ig_status}"
            ),
            parse_mode="Markdown",
        )

    # --- FINAL REJECT (second approver rejected) ---
    elif action == "reject_final":
        slot_num = slot_idx + 1
        await query.edit_message_caption(
            caption=f"❌ *Story #{slot_num} rad etildi.*",
            parse_mode="Markdown",
        )
        logger.info("Story #%d rejected by second approver", slot_num)

        await context.bot.send_message(
            chat_id=TELEGRAM_USER_ID,
            text=(
                f"❌ *Story #{slot_num} ikkinchi shaxs tomonidan rad etildi.*\n"
                "Qayta yaratish yoki o'tkazib yuborishingiz mumkin."
            ),
            parse_mode="Markdown",
        )

    # --- REGENERATE ---
    elif action == "regen":
        story        = _story_slots.get(slot_idx)
        feature_name = story["feature_name"] if story else "Noma'lum"
        feature_desc = next((f[1] for f in IIKO_FEATURES if f[0] == feature_name), "")

        await query.edit_message_caption(
            caption=f"🔄 *Story #{slot_num} qayta yaratilmoqda...*",
            parse_mode="Markdown",
        )
        try:
            new_story = await build_story(feature_name, feature_desc)
            _story_slots[slot_idx] = new_story

            photo_buf = io.BytesIO(new_story["image_bytes"])
            photo_buf.name = f"story_{slot_num}_regen.jpg"
            photo_buf.seek(0)

            await query.delete_message()
            await context.bot.send_photo(
                chat_id=TELEGRAM_USER_ID,
                photo=photo_buf,
                caption=_story_caption(slot_num, new_story, " _(yangilandi)_"),
                parse_mode="Markdown",
                reply_markup=_primary_keyboard(slot_idx),
            )
        except Exception as e:
            logger.error("Regen error #%d: %s", slot_num, e)
            await context.bot.send_message(
                chat_id=TELEGRAM_USER_ID,
                text=f"⚠️ Story #{slot_num} qayta yaratishda xatolik: {e}",
            )

    # --- SKIP ---
    elif action == "skip":
        await query.edit_message_caption(
            caption=f"❌ *Story #{slot_num} o'tkazib yuborildi.*",
            parse_mode="Markdown",
        )
        logger.info("Story #%d skipped", slot_num)

    # --- EDIT ---
    elif action == "edit":
        story = _story_slots.get(slot_idx)
        if not story:
            await query.edit_message_caption(caption=f"⚠️ Story #{slot_num} topilmadi.")
            return

        context.user_data["awaiting_edit"] = slot_idx
        logger.info("Edit mode activated for story #%d", slot_num)

        await context.bot.send_message(
            chat_id=TELEGRAM_USER_ID,
            text=(
                f"✏️ *Story #{slot_num} tahrirlash*\n\n"
                "Qanday o'zgartirish kerak? Masalan:\n"
                "• _\"Sarlavhani qisqartir\"_\n"
                "• _\"Rasm promtini restoran oshxonasiga o'zgartir\"_\n"
                "• _\"Taglavhani ingliz tilida yoz\"_\n\n"
                "So'rovingizni yozing:"
            ),
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# Text message handler — receives the edit instruction
# ---------------------------------------------------------------------------

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in (TELEGRAM_USER_ID, SECOND_APPROVER_ID):
        return

    slot_idx = context.user_data.pop("awaiting_edit", None)
    if slot_idx is None:
        return

    edit_request = update.message.text.strip()
    slot_num     = slot_idx + 1
    story        = _story_slots.get(slot_idx)

    if not story:
        await update.message.reply_text(f"⚠️ Story #{slot_num} topilmadi.")
        return

    await update.message.reply_text(
        f"✏️ *Story #{slot_num} tahrirlanmoqda...*\nBiroz kuting.",
        parse_mode="Markdown",
    )

    try:
        edited_story = await build_edited_story(story, edit_request)
        _story_slots[slot_idx] = edited_story

        photo_buf = io.BytesIO(edited_story["image_bytes"])
        photo_buf.name = f"story_{slot_num}_edited.jpg"
        photo_buf.seek(0)

        await context.bot.send_photo(
            chat_id=TELEGRAM_USER_ID,
            photo=photo_buf,
            caption=_story_caption(slot_num, edited_story, " _(tahrirlandi)_"),
            parse_mode="Markdown",
            reply_markup=_primary_keyboard(slot_idx),
        )
        logger.info("Story #%d edited and resent", slot_num)

    except Exception as e:
        logger.error("Edit error for story #%d: %s", slot_num, e)
        await update.message.reply_text(
            f"⚠️ Story #{slot_num} tahrirlashda xatolik: {e}"
        )


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    return app
