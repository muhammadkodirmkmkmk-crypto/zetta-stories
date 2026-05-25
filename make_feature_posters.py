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
        "slug":   "xodimlar_nazorati",
        "name":   "Xodimlar nazorati",
        "slogan": "Jamoangizni iiko bilan samarali boshqaring",
        "hint":   "restaurant manager in bright modern restaurant reviewing staff schedule on a tablet, calm professional expression",
    },
    {
        "slug":   "zaxira_hisobi",
        "name":   "Zaxira hisobi",
        "slogan": "Ombor nazorati orqali foyda ko'paytiring",
        "hint":   "restaurant owner in bright kitchen checking inventory on a digital tablet, natural daylight",
    },
    {
        "slug":   "z_hisobot",
        "name":   "Z-hisobot",
        "slogan": "Smena hisobotini soniyalarda xatosiz oling",
        "hint":   "restaurant cashier in bright modern cafe reviewing daily shift summary on screen, confident pose",
    },
    {
        "slug":   "buyurtmalar_tahlili",
        "name":   "Buyurtmalar tahlili",
        "slogan": "Sotuvlarni tahlil qilib ikki baravar o'siring",
        "hint":   "restaurant manager in bright dining room analyzing sales charts on laptop, floor-to-ceiling windows behind",
    },
    {
        "slug":   "meny_boshqaruvi",
        "name":   "Meny boshqaruvi",
        "slogan": "Menyuni bir marta sozlab hamma joyga yeting",
        "hint":   "restaurant owner in bright modern restaurant updating digital menu on tablet, white walls and plants in background",
    },
    {
        "slug":   "moliyaviy_nazorat",
        "name":   "Moliyaviy nazorat",
        "slogan": "Moliya nazoratini iiko orqali ishonch bilan",
        "hint":   "confident restaurant owner in bright airy restaurant reviewing financial dashboard on laptop, natural window light",
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
        f"Bright clean minimal restaurant interior photography, 9:16 vertical portrait format. "
        f"{hint}. "
        "Setting: modern upscale restaurant interior, extremely bright and airy, "
        "large floor-to-ceiling windows flooding the scene with soft natural daylight. "
        "White walls, light blond wooden chairs, white marble tables, lush green indoor plants, "
        "beautifully blurred soft-focus background with shallow depth of field. "
        "Person wears a dark navy apron over a clean white shirt. "
        "Uzbek male, Central Asian features, dark straight hair, clean-shaven, aged 30-40, "
        "calm confident smile facing camera directly. "
        "NO headset, NO headphones, NO earpiece, NO earbuds, NO glasses. "
        "Color palette: white, ivory, cream, ash wood tones, deep navy — "
        "absolutely NO red, NO pink, NO dark color grading. "
        "Mood: clean, bright, professional, trustworthy. "
        "NOT dramatic, NOT moody, NOT dark, NOT high-contrast. "
        "NO overlays, NO gradients, NO text, NO logos, NO watermarks. "
        "Photorealistic editorial magazine quality, 4K sharp, natural colors."
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
def compose(photo_bytes: bytes, feat: dict) -> Image.Image:
    # ── Photo fills entire frame ──────────────────────────────────────────
    photo_img = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
    photo_img = ImageOps.fit(photo_img, (W, H), method=Image.LANCZOS, centering=(0.5, 0.3))
    canvas    = photo_img.convert("RGBA")
    draw      = ImageDraw.Draw(canvas)

    MAX_TW = W - 60   # 640px usable width
    WHITE  = (255, 255, 255, 255)

    def _centered(text, font, y, color=WHITE, shadow_alpha=170):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw   = bbox[2] - bbox[0]
        x    = (W - tw) // 2
        draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, shadow_alpha))
        draw.text((x, y), text, font=font, fill=color)

    # ── TEXT ELEMENT 1: "Z E T T A  ×  iiko" — 3-part logo, top center ──
    logo_y  = 52
    f_zetta = _font(38, bold=False)
    f_sep   = _font(32, bold=False)
    f_iiko  = _font(38, bold=True)
    t_zetta, t_sep, t_iiko = "Z E T T A", "  ×  ", "iiko"
    w_z = draw.textbbox((0, 0), t_zetta, font=f_zetta)[2]
    w_s = draw.textbbox((0, 0), t_sep,   font=f_sep)[2]
    w_i = draw.textbbox((0, 0), t_iiko,  font=f_iiko)[2]
    lx  = (W - w_z - w_s - w_i) // 2
    SHD      = (0, 0, 0, 130)
    LOGO_COL = (255, 255, 255, 220)
    for txt, fnt, col in [(t_zetta, f_zetta, LOGO_COL),
                          (t_sep,   f_sep,   LOGO_COL),
                          (t_iiko,  f_iiko,  WHITE)]:
        draw.text((lx + 2, logo_y + 2), txt, font=fnt, fill=SHD)
        draw.text((lx,     logo_y),     txt, font=fnt, fill=col)
        lx += draw.textbbox((0, 0), txt, font=fnt)[2]

    # ── TEXT ELEMENT 2: Slogan — heavy bold white, CENTER-MIDDLE ─────────
    #    Floats over person's chest. NO dark strip behind text.
    words = feat["slogan"].split()
    mid   = max(1, len(words) // 2)
    lines = [" ".join(words[:mid]), " ".join(words[mid:])] if len(words) > 3 else [feat["slogan"]]

    font_slg = _font(72, bold=True)
    for sz in (72, 62, 54, 46):
        f = _font(sz, bold=True)
        if all(draw.textbbox((0, 0), ln, font=f)[2] <= MAX_TW for ln in lines):
            font_slg = f
            break

    lh      = draw.textbbox((0, 0), lines[0], font=font_slg)[3]
    gap     = 8
    total_h = lh * len(lines) + gap * (len(lines) - 1)
    slg_y   = H // 2 - total_h // 2
    for line in lines:
        _centered(line, font_slg, slg_y, color=WHITE, shadow_alpha=210)
        slg_y += lh + gap

    return canvas.convert("RGB")


# ── main ──────────────────────────────────────────────────────────────────
def main():
    out_paths = []
    for idx, feat in enumerate(FEATURES, 1):
        print(f"\n[{idx}/6] {feat['name']}")
        photo_bytes = generate_photo(feat["hint"])
        print("  Compositing …")
        poster = compose(photo_bytes, feat)
        out_path = os.path.join(ASSETS_DIR, f"poster_{feat['slug']}.jpg")
        poster.save(out_path, format="JPEG", quality=95)
        out_paths.append(out_path)
        print(f"  Saved → {out_path}")

    print(f"\nDone! {len(out_paths)} posters generated.")
    return out_paths


if __name__ == "__main__":
    main()
