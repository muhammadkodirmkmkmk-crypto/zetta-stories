"""
Generate 6 ZETTA×iiko feature posters (700×1244px, 9:16).

Layout:
  TOP BAR  (90px): white background — ZETTA logo left, iiko logo right
  PHOTO    (fills rest): bright minimal restaurant interior, navy apron person
  BOTTOM   (380px): dark overlay → stat number + subtitle + slogan + website

Run from zetta-bot/:  python3 make_feature_posters.py
Outputs: assets/poster_<slug>.jpg  (6 files)
"""

import os, io, sys, textwrap
import fal_client
import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps
import numpy as np

# ── dimensions ──────────────────────────────────────────────────────────────
W, H        = 700, 1244
TOP_H       = 90    # white logo bar
PHOTO_TOP   = TOP_H
BOT_H       = 380   # dark text strip at bottom
PHOTO_BOT   = H - BOT_H

FAL_KEY = os.environ.get("FAL_KEY", "")
if not FAL_KEY:
    sys.exit("FAL_KEY env var not set")

_HERE      = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(_HERE, "assets")
os.makedirs(ASSETS_DIR, exist_ok=True)

# ── 6 feature definitions ─────────────────────────────────────────────────
FEATURES = [
    {
        "slug":     "xodimlar_nazorati",
        "name":     "Xodimlar nazorati",
        "stat":     "30%",
        "stat_label": "samaradorlik oshadi",
        "subtitle": "Har bir xodim ishi real vaqtda nazorat ostida",
        "slogan":   "Jamoangizni iiko bilan boshqaring",
        "hint":     "restaurant manager in bright modern restaurant reviewing staff schedule on a tablet, calm professional expression",
    },
    {
        "slug":     "zaxira_hisobi",
        "name":     "Zaxira hisobi",
        "stat":     "15%",
        "stat_label": "isrof kamayadi",
        "subtitle": "Mahsulot qoldig'ini avtomatik hisoblab chiqadi",
        "slogan":   "Ombor nazorati — foyda ko'paytiradi",
        "hint":     "restaurant owner in bright kitchen checking inventory on a digital tablet, natural daylight",
    },
    {
        "slug":     "z_hisobot",
        "name":     "Z-hisobot",
        "stat":     "5 min",
        "stat_label": "smena yopish vaqti",
        "subtitle": "Kun oxirida hisobot avtomatik tayyor bo'ladi",
        "slogan":   "Hisobot — soniyalarda, xatosiz",
        "hint":     "restaurant cashier in bright modern cafe reviewing daily shift summary on screen, confident pose",
    },
    {
        "slug":     "buyurtmalar_tahlili",
        "name":     "Buyurtmalar tahlili",
        "stat":     "2x",
        "stat_label": "sotuvlar o'sishi",
        "subtitle": "Eng ko'p sotilgan taomlarni bilib oling",
        "slogan":   "Ma'lumotga asoslangan qarorlar qabul qiling",
        "hint":     "restaurant manager in bright dining room analyzing sales charts on laptop, floor-to-ceiling windows behind",
    },
    {
        "slug":     "meny_boshqaruvi",
        "name":     "Meny boshqaruvi",
        "stat":     "100+",
        "stat_label": "menyu elementi",
        "subtitle": "Menyuni bir marta sozlang, hamma joyda ishlaydi",
        "slogan":   "Tez, oson, moslashuvchan meny tizimi",
        "hint":     "restaurant owner in bright modern restaurant updating digital menu on tablet, white walls and plants in background",
    },
    {
        "slug":     "moliyaviy_nazorat",
        "name":     "Moliyaviy nazorat",
        "stat":     "24/7",
        "stat_label": "monitoring",
        "subtitle": "Daromad va xarajatlar real vaqtda ko'rinadi",
        "slogan":   "Moliya nazoratini iiko ga ishoning",
        "hint":     "confident restaurant owner in bright airy restaurant reviewing financial dashboard on laptop, natural window light",
    },
]

# ── font helper ───────────────────────────────────────────────────────────
def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    candidates = [
        os.path.join(_HERE, "fonts", "Montserrat-Bold.ttf") if bold else None,
        os.path.join(_HERE, "fonts", "DejaVuSans-Bold.ttf") if bold else None,
        os.path.join(_HERE, "fonts", "DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else None,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if c and os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except OSError:
                continue
    return ImageFont.load_default()


# ── logo preparation (done once) ─────────────────────────────────────────
def _prepare_logos() -> tuple[Image.Image, Image.Image]:
    """
    Returns (zetta_logo, iiko_logo) as RGBA images sized for the 90px top bar.
    """
    bar_inner = TOP_H - 24   # 66px usable height inside bar

    # ── ZETTA logo: black text on white — auto-crop to content ───────────
    z_raw = Image.open(os.path.join(ASSETS_DIR, "zetta_logo.jpg")).convert("RGB")
    # Find non-white bounding box
    z_np  = np.array(z_raw)
    mask  = (z_np.max(axis=2) < 240)   # dark pixels
    rows  = np.where(mask.any(axis=1))[0]
    cols  = np.where(mask.any(axis=0))[0]
    if rows.size and cols.size:
        pad = 20
        r0, r1 = max(0, rows[0]-pad), min(z_raw.height, rows[-1]+pad)
        c0, c1 = max(0, cols[0]-pad), min(z_raw.width,  cols[-1]+pad)
        z_raw  = z_raw.crop((c0, r0, c1, r1))
    # Resize to bar height, keep aspect ratio
    z_w = int(z_raw.width * bar_inner / z_raw.height)
    z_logo = z_raw.resize((z_w, bar_inner), Image.LANCZOS).convert("RGBA")

    # ── iiko logo: red square — crop to square content, resize ───────────
    i_raw  = Image.open(os.path.join(ASSETS_DIR, "iiko_logo.jpg")).convert("RGBA")
    # Crop to square from center
    iw, ih = i_raw.size
    sq     = min(iw, ih)
    left   = (iw - sq) // 2
    top    = (ih - sq) // 2
    i_raw  = i_raw.crop((left, top, left + sq, top + sq))
    i_logo = i_raw.resize((bar_inner, bar_inner), Image.LANCZOS)

    return z_logo, i_logo


# ── background photo generation ──────────────────────────────────────────
def generate_photo(hint: str) -> bytes:
    prompt = (
        f"Bright minimal professional restaurant portrait photography, 9:16 vertical. "
        f"{hint}. "
        "Setting: modern upscale restaurant, very bright and airy, large floor-to-ceiling windows "
        "flooding the scene with soft natural daylight. White walls, light blond wooden chairs, "
        "white marble tables, lush green indoor plants, blurred soft-focus background. "
        "Color palette: white, ivory, cream, ash wood, deep navy apron. "
        "Person is sharp and well-lit, background is bokeh. "
        "Mood: clean, bright, professional, trustworthy. NOT dramatic, NOT moody, NOT dark. "
        "NO red color grading, NO dark overlays, NO text, NO logos. "
        "Photorealistic, editorial magazine quality, 4K sharp."
    )
    print(f"    fal.ai generating photo …")
    result = fal_client.run(
        "fal-ai/flux-pro",
        arguments={
            "prompt": prompt,
            "image_size": {"width": W, "height": H - TOP_H},   # photo fills below the bar
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
            "num_images": 1,
            "enable_safety_checker": True,
        },
    )
    url  = result["images"][0]["url"]
    resp = httpx.get(url, timeout=60)
    resp.raise_for_status()
    print(f"    downloaded {len(resp.content)//1024}KB")
    return resp.content


# ── poster composition ────────────────────────────────────────────────────
def compose(photo_bytes: bytes, feat: dict,
            z_logo: Image.Image, i_logo: Image.Image) -> Image.Image:

    # ── canvas (white start) ──────────────────────────────────────────────
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    draw   = ImageDraw.Draw(canvas)

    # ── TOP BAR: white 90px ────────────────────────────────────────────────
    # already white; paste logos
    pad = 16
    # ZETTA logo — left side
    z_y = (TOP_H - z_logo.height) // 2
    canvas.paste(z_logo, (pad, z_y), z_logo)
    # iiko logo — right side
    i_y = (TOP_H - i_logo.height) // 2
    canvas.paste(i_logo, (W - pad - i_logo.width, i_y), i_logo)
    # thin separator between logo bar and photo
    draw.line([(0, TOP_H - 1), (W, TOP_H - 1)], fill=(220, 220, 220, 255), width=1)

    # ── PHOTO: fills y=90..1244 ────────────────────────────────────────────
    photo_img = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
    photo_img = ImageOps.fit(photo_img, (W, H - TOP_H), method=Image.LANCZOS, centering=(0.5, 0.2))
    canvas.paste(photo_img, (0, TOP_H))

    # ── BOTTOM overlay: dark gradient for text readability ─────────────────
    bot_arr = np.zeros((BOT_H, W, 4), dtype=np.uint8)
    for y in range(BOT_H):
        alpha = int(210 * (y / BOT_H) ** 0.55)
        bot_arr[y, :, 3] = alpha
    bot_overlay = Image.fromarray(bot_arr, "RGBA")
    canvas.paste(bot_overlay, (0, PHOTO_BOT), bot_overlay)

    draw = ImageDraw.Draw(canvas)

    # ── text helpers ────────────────────────────────────────────────────────
    WHITE      = (255, 255, 255, 255)
    WHITE_DIM  = (230, 230, 230, 255)
    MAX_TW     = W - 60   # 640px

    def _centered(text, font, y, color=WHITE, shadow_alpha=140):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw   = bbox[2] - bbox[0]
        x    = (W - tw) // 2
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, shadow_alpha))
        draw.text((x, y), text, font=font, fill=color)

    def _fit_font(text, sizes, bold=True):
        for sz in sizes:
            f = _font(sz, bold=bold)
            if draw.textbbox((0, 0), text, font=f)[2] <= MAX_TW:
                return f, sz
        return _font(sizes[-1], bold=bold), sizes[-1]

    # ── STAT (huge number) ─────────────────────────────────────────────────
    stat_y    = PHOTO_BOT + 20
    font_stat, _ = _fit_font(feat["stat"], (96, 80, 68), bold=True)
    _centered(feat["stat"], font_stat, stat_y, color=WHITE)

    # ── STAT LABEL (small, below stat) ────────────────────────────────────
    stat_bbox = draw.textbbox((0, 0), feat["stat"], font=font_stat)
    label_y   = stat_y + (stat_bbox[3] - stat_bbox[1]) + 4
    font_lbl, _ = _fit_font(feat["stat_label"], (22, 18), bold=False)
    _centered(feat["stat_label"], font_lbl, label_y, color=WHITE_DIM, shadow_alpha=100)

    # ── SUBTITLE ──────────────────────────────────────────────────────────
    sub_y = label_y + 30 + 12
    font_sub, _ = _fit_font(feat["subtitle"], (24, 20, 18), bold=False)
    _centered(feat["subtitle"], font_sub, sub_y, color=WHITE_DIM)

    # ── SLOGAN (bold) ─────────────────────────────────────────────────────
    slg_y = sub_y + 34
    font_slg, slg_sz = _fit_font(feat["slogan"], (28, 24, 20), bold=True)
    _centered(feat["slogan"], font_slg, slg_y, color=WHITE)

    # ── WEBSITE ───────────────────────────────────────────────────────────
    web_y    = H - 44
    font_web = _font(18, bold=False)
    _centered("zetta.uz — bepul konsultatsiya", font_web, web_y,
              color=(210, 210, 210, 255), shadow_alpha=80)

    return canvas.convert("RGB")


# ── main ──────────────────────────────────────────────────────────────────
def main():
    print("Preparing logos …")
    z_logo, i_logo = _prepare_logos()
    print(f"  ZETTA logo: {z_logo.size}   iiko logo: {i_logo.size}")

    out_paths = []
    for idx, feat in enumerate(FEATURES, 1):
        print(f"\n[{idx}/6] {feat['name']}")
        photo_bytes = generate_photo(feat["hint"])
        print("  Compositing …")
        poster = compose(photo_bytes, feat, z_logo, i_logo)
        out_path = os.path.join(ASSETS_DIR, f"poster_{feat['slug']}.jpg")
        poster.save(out_path, format="JPEG", quality=95)
        out_paths.append(out_path)
        print(f"  Saved → {out_path}")

    print(f"\nDone! {len(out_paths)} posters generated.")
    return out_paths


if __name__ == "__main__":
    main()
