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
    CommandHandler,
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
    # Стоп-листы и меню
    ("Stop-list boshqaruvi",      "real-time menu stop-list management, out-of-stock blocking"),
    ("Menyu muhandisligi",        "menu engineering profit margin optimization, dish placement"),
    ("Modifikatorlar va kombo",   "modifiers combo dishes upsell customization"),
    ("Onlayn menyu QR",           "iikoWeb QR online menu contactless ordering"),
    # ABC-анализ
    ("ABC tahlil",                "ABC analysis dish profitability popularity ranking"),
    ("Sotish tahlili",            "sales trend analysis top sellers slow movers"),
    # KPI и персонал
    ("Ofitsiant KPI",             "waiter motivation KPI tips upsell performance bonus"),
    ("Xodim jadvali",             "staff shift scheduling automated planning"),
    ("Xodimlarni boshqarish",     "staff management HR access roles permissions"),
    # Лояльность
    ("iikoCard sodiqlik",         "loyalty program iikoCard points bonuses discounts"),
    ("RKEEP sodiqlik",            "RKEEP external loyalty system integration"),
    ("Chegirmalar va aksiyalar",  "discounts promotions happy hour marketing campaigns"),
    ("Mijoz ma'lumotlar bazasi",  "customer database CRM repeat guest personalization"),
    # Банкеты и предзаказы
    ("Banket va oldinbron",       "banquet hall pre-order deposit management"),
    ("Stol boshqaruvi",          "table map reservations seating floor management"),
    ("Onlayn bron",               "online reservation booking widget integration"),
    # Пречек
    ("Pretchek texnikasi",        "pre-check printing guest objection handling technique"),
    # Delivery
    ("Yetkazib berish",           "iikoDelivery courier dispatch aggregator integration"),
    ("Koll-markaz",               "call center order taking delivery management"),
    ("Agregator integratsiya",    "Yandex Delivery Uzum aggregator menu sync automation"),
    # Фудкост и склад
    ("Fudkost nazorati",          "food cost calculation recipe costing gross profit"),
    ("Inventarizatsiya",          "inventory stocktake warehouse reconciliation"),
    ("Ombor boshqaruvi",          "warehouse stock tracking auto-orders supplier management"),
    ("Avtomatik buyurtma",        "automatic purchase order reorder point supply chain"),
    # Отчёты
    ("Smena hisobotlari",         "shift revenue Z-report cashier close analytics"),
    ("Moliyaviy hisobotlar",      "P&L financial reports online real-time dashboard"),
    ("Kengaytirilgan hisobotlar", "advanced BI analytics custom reports export"),
    # Антифрод и касса
    ("Antifrod tizimi",           "anti-fraud void discount abuse cashier audit"),
    ("Kassa iikoFront",           "POS system front office cashier order entry"),
    ("Kassa nazorati",            "cash drawer control security blind close audit"),
    # Интеграции
    ("SmartControl",              "real-time smartphone remote restaurant monitoring"),
    ("API integratsiya",          "third party API webhook automation integrations"),
    ("1C integratsiya",           "1C accounting CRM ERP two-way data sync"),
    ("Tarmoq boshqaruvi",         "iikoChain multi-location central kitchen management"),
    # Управление сетью/франшизой
    ("Franshiza boshqaruvi",      "franchise management central menu pricing control"),
    ("Reklama effektivligi",      "marketing ROI campaign attribution analytics"),
    # Прочее
    ("Mijoz fikri",               "customer feedback rating review response management"),
    ("Buyurtma tarixi",           "order history guest preferences repeat visit tracking"),
    ("Moliyaviy nazorat",         "budget planning financial forecast cost control"),
]

# ---------------------------------------------------------------------------
# Title format rotation (never repeat same format twice in a row)
# ---------------------------------------------------------------------------

_TITLE_FORMATS = [
    ("question",    "Savol shakli: 'Nima uchun 80% restoranlar [mavzu]da pul yo'qotadi?' — qisqa, hayratga soluvchi savol"),
    ("provocation", "Provokatsiya: 'Sen hali [mavzu]ni qo'lda qilyapsanmi?' — to'g'ridan-to'g'ri murojaat, o'tkir ton"),
    ("number",      "Raqam: '3 sabab nima uchun [mavzu] restoranlarni o'zgartiradi' — aniq raqam bilan boshlash"),
    ("intrigue",    "Intriga: '[mavzu]ni yoqsang nima bo'lishini ko'r' — nima sodir bo'lishini bilmoqchi qilish"),
    ("fact_pain",   "Fakt + og'riq: 'iiko buni 5 yildan beri uddalaydi. Bilarmiding?' — eski muammo, tayyor yechim"),
    ("command",     "Buyruq: 'Bugun iiko da [mavzu]ni yoq' — qisqa, to'g'ridan-to'g'ri call-to-action"),
]
_last_title_fmt: list[int] = [-1]   # mutable container so nested functions can update


def _next_title_format() -> tuple[str, str]:
    """Return the next title format, never repeating the previous one."""
    choices = [i for i in range(len(_TITLE_FORMATS)) if i != _last_title_fmt[0]]
    idx = random.choice(choices)
    _last_title_fmt[0] = idx
    return _TITLE_FORMATS[idx]

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
    montserrat_bold = os.path.join(_here, "fonts", "Montserrat-Bold.ttf")
    bundled_bold    = os.path.join(_here, "fonts", "DejaVuSans-Bold.ttf")
    bundled_regular = os.path.join(_here, "fonts", "DejaVuSans.ttf")

    candidates = (
        [
            montserrat_bold,                                           # preferred
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
            InlineKeyboardButton("🚀 Tezkor nashr",      callback_data=f"quick_publish:{slot_idx}"),
        ],
        [
            InlineKeyboardButton("🔄 Qaytaratish",       callback_data=f"regen:{slot_idx}"),
            InlineKeyboardButton("❌ Otkazish",           callback_data=f"skip:{slot_idx}"),
        ],
        [
            InlineKeyboardButton("✏️ Tahrirlash",        callback_data=f"edit:{slot_idx}"),
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
    t_top  = story.get("title_top", "")
    t_main = story.get("title_main", story.get("title", ""))
    t_bot  = story.get("title_bottom", "")
    parts  = " / ".join(p for p in [t_top, t_main, t_bot] if p)
    return (
        f"📸 *Story #{slot_num}*{suffix}\n\n"
        f"🏷 *Xususiyat:* {story['feature_name']}\n"
        f"📝 *Sarlavha:*\n"
        f"   _{t_top}_\n"
        f"   *{t_main}*\n"
        f"   _{t_bot}_\n"
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


def compose_story_image(
    photo_bytes: bytes,
    title_top: str,
    title_main: str,
    title_bottom: str,
) -> bytes:
    import re
    # ── 1. Photo fills entire 1080×1920 canvas (cover crop, no stretch) ──────
    from PIL import ImageOps
    photo = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    photo = ImageOps.fit(photo, (IMAGE_W, IMAGE_H), method=Image.LANCZOS, centering=(0.5, 0.3))

    # ── 2. Red gradient: solid #A70D19 y=0-280, fade to transparent by y=580 ─
    SOLID_H  = 280
    FADE_H   = 580
    y_idx    = np.arange(IMAGE_H, dtype=np.float32)
    fade     = np.clip((y_idx - SOLID_H) / (FADE_H - SOLID_H), 0.0, 1.0)
    alpha_1d = np.clip(180.0 * (1.0 - fade), 0, 180).astype(np.uint8)

    overlay_arr = np.zeros((IMAGE_H, IMAGE_W, 4), dtype=np.uint8)
    overlay_arr[:, :, 0] = 0xA7   # #A70D19
    overlay_arr[:, :, 1] = 0x0D
    overlay_arr[:, :, 2] = 0x19
    overlay_arr[:, :, 3] = alpha_1d[:, np.newaxis]

    overlay = Image.fromarray(overlay_arr, mode="RGBA")
    canvas  = Image.alpha_composite(photo.convert("RGBA"), overlay)
    draw    = ImageDraw.Draw(canvas)

    MAX_W = IMAGE_W - 120  # 960px (60px padding each side)

    def _centered(d, text, font, y, color=(255, 255, 255, 255), shadow_alpha=150):
        bbox = d.textbbox((0, 0), text, font=font)
        x = (IMAGE_W - (bbox[2] - bbox[0])) // 2
        d.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, shadow_alpha))
        d.text((x, y), text, font=font, fill=color)

    def _clean(text: str) -> str:
        return re.sub(r"[^\w\s'`'\u2018\u2019\u02bc]", "", text).strip()

    def _fit_1line(text: str, sizes: tuple, bold: bool):
        """Return (font, size) for the largest size where text fits MAX_W."""
        for sz in sizes:
            f = _find_font(bold=bold, size=sz)
            if draw.textbbox((0, 0), text, font=f)[2] <= MAX_W:
                return f, sz
        f = _find_font(bold=bold, size=sizes[-1])
        return f, sizes[-1]

    # ── 3. Logo ───────────────────────────────────────────────────────────────
    _centered(draw, "Z E T T A", _find_font(bold=False, size=60), y=186)

    # ── 4. Three-layer title hierarchy ────────────────────────────────────────
    #
    #   title_top   — small intro/hook, white 85%, ~30px, normal
    #   title_main  — hero keyword(s), HUGE bold yellow, 90-96px
    #   title_bottom — qualifier/detail, medium white bold, ~42px
    #
    y_cursor = 246   # start just below logo+padding

    # --- Layer 1: title_top ---
    top_clean = _clean(title_top)
    if top_clean:
        font_top, _ = _fit_1line(top_clean, (32, 28, 24), bold=False)
        _centered(draw, top_clean, font_top, y=y_cursor,
                  color=(255, 255, 255, 217), shadow_alpha=90)
        top_h = draw.textbbox((0, 0), top_clean, font=font_top)[3]
        y_cursor += top_h + 14
    else:
        y_cursor += 10

    # --- Layer 2: title_main (hero) ---
    main_clean = _clean(title_main).upper()
    # Try single-line first at progressively smaller sizes; fall back to 2 lines
    font_main, main_sz = _fit_1line(main_clean, (96, 86, 76, 66), bold=True)
    main_words = main_clean.split()
    main_lines = [main_clean]

    if draw.textbbox((0, 0), main_clean, font=font_main)[2] > MAX_W and len(main_words) >= 2:
        # Try 2-line split at the largest size that fits both halves
        mid = max(1, len(main_words) // 2)
        l1, l2 = " ".join(main_words[:mid]), " ".join(main_words[mid:])
        for sz in (86, 76, 66, 56):
            f = _find_font(bold=True, size=sz)
            if draw.textbbox((0,0), l1, font=f)[2] <= MAX_W and \
               draw.textbbox((0,0), l2, font=f)[2] <= MAX_W:
                font_main, main_sz = f, sz
                main_lines = [l1, l2]
                break

    YELLOW = (255, 210, 50, 255)
    for line in main_lines:
        _centered(draw, line, font_main, y=y_cursor, color=YELLOW, shadow_alpha=180)
        y_cursor += main_sz + 10
    y_cursor += 8   # extra gap before bottom layer

    # --- Layer 3: title_bottom ---
    bot_clean = _clean(title_bottom)
    if bot_clean:
        font_bot, _ = _fit_1line(bot_clean, (44, 38, 32, 28), bold=True)
        _centered(draw, bot_clean, font_bot, y=y_cursor,
                  color=(255, 255, 255, 255), shadow_alpha=140)

    # ── 5. Output ─────────────────────────────────────────────────────────────
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
    fmt_name, fmt_instruction = _next_title_format()
    logger.info("Using title format: %s", fmt_name)
    prompt = f"""Sen Zetta Group uchun Instagram Stories kontent yaratuvchisan.
Zetta Group — O'zbekistondagi iiko rasmiy hamkori. Restoran biznesini avtomatlashtirish yechimlari.

Quyidagi iiko xususiyati uchun kontent yarat:
- Xususiyat: {feature_name} ({feature_desc})

SARLAVHA FORMATI — bugun: {fmt_name}
{fmt_instruction}

SARLAVHANI 3 QISMGA BO'L (vizual ierarxiya):
- title_top   : qisqa kirish ibora yoki savol boshi, 2-4 so'z, kichik harflar ok
                Misol: "sen hali" / "bilasanmi" / "har kuni" / "3 sabab"
- title_main  : asosiy KALIT SO'Z yoki max 2 so'z, KATTA HARFLAR, juda qisqa va kuchli
                Misol: "STOP LIST" / "FUDKOST" / "KPI" / "DELIVERY"
                Bu eng katta va yorqin bo'ladi — rasmdagi asosiy e'tibor shu!
- title_bottom: oydinlashtiruvchi ibora, 3-6 so'z
                Misol: "qo'lda yozyapsanmi" / "restoranlarni o'zgartiradi" / "hamma narsani biladi"

Muhim qoidalar:
- Hech qanday belgi yo'q: tire, nuqta, vergul, undov, savol. Faqat so'zlar va apostrof (')
- title_main MAKSIMAL 2-3 so'z, qisqa va kuchli
- Uch qism birga o'qilganda mantiqli gap hosil qilsin

Faqat JSON qaytargin, hech qanday izoh yo'q:
{{
  "title_top": "...",
  "title_main": "...",
  "title_bottom": "...",
  "image_prompt": "Detailed English prompt for photorealistic restaurant or business scene related to {feature_name}. Professional photography, warm lighting, elegant interior, staff using technology, no text in image, 4k quality."
}}"""

    response = claude_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    raw  = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    data = json.loads(raw)
    logger.info("Content generated: top=%s | main=%s | bottom=%s",
                data.get("title_top"), data.get("title_main"), data.get("title_bottom"))
    return data


def generate_edited_content(story: dict, edit_request: str) -> dict:
    logger.info("Editing story with request: %s", edit_request)
    prompt = f"""Quyidagi Instagram Story kontentini foydalanuvchi so'roviga ko'ra tahrirlash kerak.

Mavjud kontent:
- feature_name: {story['feature_name']}
- title_top: {story.get('title_top', '')}
- title_main: {story.get('title_main', story.get('title', ''))}
- title_bottom: {story.get('title_bottom', '')}
- image_prompt: {story['image_prompt']}

Foydalanuvchi so'rovi: {edit_request}

Faqat o'zgartirilishi kerak bo'lgan maydonlarni yangilang. 3 qismlik sarlavha tuzilmasini saqlang.
Hech qanday belgi yo'q: tire, nuqta, vergul, undov, savol. Faqat so'zlar va apostrof.

Faqat JSON qaytargin:
{{
  "title_top": "qisqa kirish ibora 2-4 so'z",
  "title_main": "ASOSIY KALIT 1-2 SOZ",
  "title_bottom": "oydinlashtiruvchi 3-6 so'z",
  "image_prompt": "Detailed English prompt..."
}}"""

    response = claude_client.messages.create(
        model="claude-opus-4-5",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    raw  = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    data = json.loads(raw)
    logger.info("Edited content: top=%s | main=%s | bottom=%s",
                data.get("title_top"), data.get("title_main"), data.get("title_bottom"))
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
# Publishing via Make.com webhook (imgbb → Make.com → Instagram)
# ---------------------------------------------------------------------------

MAKE_WEBHOOK_URL = "https://hook.eu1.make.com/ray8bn6v77mem726aspsk83up95o1x7j"


def _publish_via_make_sync(image_bytes: bytes, slot_num: int) -> str | None:
    """
    Upload image to imgbb, then POST the URL to Make.com webhook.
    Returns None on success, error string on failure.
    """
    import tempfile
    import requests
    import traceback

    imgbb_key = os.environ.get("IMGBB_API_KEY", "").strip()
    if not imgbb_key:
        return "IMGBB_API_KEY not set in Railway environment"

    # ── Ensure image is JPEG 1080×1920 ───────────────────────────────────────
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        if img.size != (1080, 1920):
            logger.warning("Resizing image from %s to 1080x1920", img.size)
            img = img.resize((1080, 1920), Image.LANCZOS)
        jpeg_buf = io.BytesIO()
        img.save(jpeg_buf, format="JPEG", quality=95)
        jpeg_bytes = jpeg_buf.getvalue()
    except Exception as e:
        return f"Image preparation failed: {e}"

    # ── Upload to imgbb ───────────────────────────────────────────────────────
    try:
        logger.info("Uploading story #%d to imgbb...", slot_num)
        resp = requests.post(
            "https://api.imgbb.com/1/upload",
            params={"key": imgbb_key},
            files={"image": ("story.jpg", jpeg_bytes, "image/jpeg")},
            timeout=30,
        )
        resp.raise_for_status()
        image_url = resp.json()["data"]["url"]
        logger.info("Story #%d imgbb URL: %s", slot_num, image_url)
    except Exception as e:
        tb = traceback.format_exc()
        print(f"IMGBB UPLOAD ERROR:\n{tb}", flush=True)
        return f"imgbb upload failed: {type(e).__name__}: {e}"

    # ── POST to Make.com webhook ──────────────────────────────────────────────
    try:
        logger.info("Sending story #%d to Make.com webhook...", slot_num)
        make_resp = requests.post(
            MAKE_WEBHOOK_URL,
            json={"image_url": image_url},
            timeout=30,
        )
        if make_resp.status_code == 200:
            logger.info("Story #%d accepted by Make.com (200)", slot_num)
            return None
        return f"Make.com returned HTTP {make_resp.status_code}: {make_resp.text[:200]}"
    except Exception as e:
        tb = traceback.format_exc()
        print(f"MAKE WEBHOOK ERROR:\n{tb}", flush=True)
        return f"Make.com request failed: {type(e).__name__}: {e}"


async def publish_to_instagram(image_bytes: bytes, slot_num: int) -> tuple[bool, str]:
    """Returns (success, error_message). error_message is '' on success."""
    loop = asyncio.get_event_loop()
    err  = await loop.run_in_executor(None, _publish_via_make_sync, image_bytes, slot_num)
    return (err is None), (err or "")


# ---------------------------------------------------------------------------
# Build / send story
# ---------------------------------------------------------------------------

async def build_story(feature_name: str, feature_desc: str) -> dict:
    loop    = asyncio.get_event_loop()
    content = await loop.run_in_executor(None, generate_story_content, feature_name, feature_desc)

    photo_bytes = await generate_fal_image(content["image_prompt"])
    composed    = compose_story_image(
        photo_bytes,
        content["title_top"],
        content["title_main"],
        content["title_bottom"],
    )

    return {
        "feature_name": feature_name,
        "title_top":    content["title_top"],
        "title_main":   content["title_main"],
        "title_bottom": content["title_bottom"],
        "image_prompt": content["image_prompt"],
        "image_bytes":  composed,
    }


async def build_edited_story(story: dict, edit_request: str) -> dict:
    loop    = asyncio.get_event_loop()
    content = await loop.run_in_executor(None, generate_edited_content, story, edit_request)

    photo_bytes = await generate_fal_image(content["image_prompt"])
    composed    = compose_story_image(
        photo_bytes,
        content["title_top"],
        content["title_main"],
        content["title_bottom"],
    )

    return {
        "feature_name": content.get("feature_name", story["feature_name"]),
        "title_top":    content["title_top"],
        "title_main":   content["title_main"],
        "title_bottom": content["title_bottom"],
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
# Feature-selection menu helpers
# ---------------------------------------------------------------------------

def _feature_menu_keyboard(features: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Build a keyboard with one button per feature, 3 per row."""
    rows = []
    row: list[InlineKeyboardButton] = []
    for feat_name, _ in features:
        # find index in full pool so callback can look it up
        idx = next((i for i, f in enumerate(IIKO_FEATURES) if f[0] == feat_name), 0)
        row.append(InlineKeyboardButton(feat_name, callback_data=f"pick_feature:{idx}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def _send_feature_menu(bot, chat_id: int) -> None:
    """Pick 15 random features and send them as a selection menu."""
    features = random.sample(IIKO_FEATURES, 15)
    kb = _feature_menu_keyboard(features)
    await bot.send_message(
        chat_id=chat_id,
        text=(
            "📋 *Bugungi mavzuni tanlang*\n\n"
            "Qaysi iiko xususiyati haqida story yarataylik?\n"
            "Tugmani bosing — bot shu mavzuda story generatsiya qiladi."
        ),
        parse_mode="Markdown",
        reply_markup=kb,
    )


# ---------------------------------------------------------------------------
# Daily menu broadcast (replaces auto-generation)
# ---------------------------------------------------------------------------

async def run_daily_generation(app):
    """Sends a 15-feature selection menu at 09:00 Tashkent instead of auto-generating."""
    logger.info("Daily menu: sending feature selection to owner...")
    await _send_feature_menu(app.bot, TELEGRAM_USER_ID)


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    action, slot_str = query.data.split(":", 1)

    # --- PICK FEATURE (from daily menu or /test) ---
    if action == "pick_feature":
        feat_idx  = int(slot_str)
        feat_name, feat_desc = IIKO_FEATURES[feat_idx]
        await query.edit_message_text(
            text=f"⏳ *{feat_name}* haqida story yaratilmoqda...\nBiroz kuting.",
            parse_mode="Markdown",
        )
        # Use next available slot index
        slot_idx = max(_story_slots.keys(), default=-1) + 1
        try:
            story = await build_story(feat_name, feat_desc)
            _story_slots[slot_idx] = story
            await send_story_for_approval(context.bot, slot_idx, story)
            logger.info("Story for '%s' generated and sent for approval", feat_name)
        except Exception as e:
            logger.error("Error building story for '%s': %s", feat_name, e)
            await context.bot.send_message(
                chat_id=TELEGRAM_USER_ID,
                text=f"⚠️ {feat_name} haqida story yaratishda xatolik: {e}",
            )
        return

    slot_idx = int(slot_str)
    slot_num = slot_idx + 1

    # --- QUICK PUBLISH — bypass second approver, publish immediately ---
    if action == "quick_publish":
        story = _story_slots.get(slot_idx)
        if not story:
            await query.edit_message_caption(caption=f"⚠️ Story #{slot_num} topilmadi.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = OUTPUT_DIR / f"story_{slot_num}_{timestamp}.jpg"
        filename.write_bytes(story["image_bytes"])
        logger.info("Story #%d saved (quick publish) → %s", slot_num, filename)

        await query.edit_message_caption(caption=f"✅ Story #{slot_num} tayyor! Fayl yuborilmoqda...")

        doc_buf = io.BytesIO(story["image_bytes"])
        doc_buf.name = f"story_{slot_num}_{timestamp}.jpg"
        doc_buf.seek(0)
        await context.bot.send_document(
            chat_id=TELEGRAM_USER_ID,
            document=doc_buf,
            caption="✅ Story tayyor! Instagram'ga qo'lda yuklang.",
        )
        logger.info("Story #%d sent as document (quick publish)", slot_num)
        return

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

        await query.edit_message_caption(caption=f"✅ Story #{slot_num} tasdiqlandi! Fayl yuborilmoqda...")

        # Save locally
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename  = OUTPUT_DIR / f"story_{slot_num}_{timestamp}.jpg"
        filename.write_bytes(story["image_bytes"])
        logger.info("Story #%d saved → %s", slot_num, filename)

        # Send full-quality image file to primary user
        doc_buf = io.BytesIO(story["image_bytes"])
        doc_buf.name = f"story_{slot_num}_{timestamp}.jpg"
        doc_buf.seek(0)
        await context.bot.send_document(
            chat_id=TELEGRAM_USER_ID,
            document=doc_buf,
            caption="✅ Story tayyor! Instagram'ga qo'lda yuklang.",
        )
        logger.info("Story #%d sent as document to primary user", slot_num)

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

    else:
        logger.warning("Unknown callback action %r — ignoring (slot=%s)", action, slot_str)


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

async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate and send 1 test story for a random iiko feature."""
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    await _send_feature_menu(context.bot, update.effective_chat.id)


def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    return app
