"""
One-off script: generate ZETTA × iiko marketing poster (700×1244px, 9:16).
Run from zetta-bot/ directory:
    python3 make_poster.py
"""
import os, io, sys, textwrap
import fal_client
import httpx
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter
import numpy as np

W, H = 700, 1244
OUT  = "zetta_iiko_poster.jpg"

FAL_KEY = os.environ.get("FAL_KEY", "")
if not FAL_KEY:
    sys.exit("FAL_KEY env var not set")

_HERE = os.path.dirname(os.path.abspath(__file__))


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
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


def generate_bg() -> bytes:
    prompt = (
        "Bright minimal restaurant portrait photography, 9:16 vertical format. "
        "Subject: a professional restaurant owner, male, mid-30s, wearing a dark navy apron over a clean crisp white shirt, "
        "standing still facing the camera, calm confident smile, relaxed arms at sides. "
        "Setting: modern upscale restaurant interior, very bright and airy, "
        "large floor-to-ceiling windows behind him flooding the scene with abundant soft natural daylight, "
        "white walls, light blond wooden chairs, round white marble-top tables, "
        "lush green indoor plants in background. "
        "The background is soft-focus bokeh — subject is sharp and well-lit. "
        "Color palette: pure white, ivory, soft cream, light ash wood tones, deep navy apron. "
        "Mood: clean, bright, professional, trustworthy — NOT dramatic, NOT moody, NOT dark. "
        "Lighting: bright even natural daylight from windows, no harsh shadows, airy editorial feel. "
        "Style: high-end restaurant branding photography, magazine quality. "
        "No text, no logos, no UI overlays, no dark color grading, photorealistic, 4K sharp."
    )
    print("Generating background via fal.ai flux-pro …")
    result = fal_client.run(
        "fal-ai/flux-pro",
        arguments={
            "prompt": prompt,
            "image_size": {"width": W, "height": H},
            "num_inference_steps": 28,
            "guidance_scale": 3.5,
            "num_images": 1,
            "enable_safety_checker": True,
        },
    )
    url = result["images"][0]["url"]
    print(f"  image URL: {url[:60]}…")
    resp = httpx.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def compose(photo_bytes: bytes) -> Image.Image:
    # ── base photo ─────────────────────────────────────────────────────────────
    photo = Image.open(io.BytesIO(photo_bytes)).convert("RGBA")
    photo = ImageOps.fit(photo, (W, H), method=Image.LANCZOS, centering=(0.5, 0.2))

    canvas = photo.copy()
    draw   = ImageDraw.Draw(canvas)

    # ── top gradient overlay — light dark band only behind text, not heavy ────
    top_h   = 300
    top_arr = np.zeros((top_h, W, 4), dtype=np.uint8)
    for y in range(top_h):
        # max alpha 140 at top (not 200), fades quickly — keeps image bright
        alpha = int(140 * max(0.0, 1.0 - y / top_h) ** 0.6)
        top_arr[y, :, 3] = alpha
    top_overlay = Image.fromarray(top_arr, "RGBA")
    canvas.paste(top_overlay, (0, 0), top_overlay)

    def centered_text(text, font, y, color=(255, 255, 255, 255), shadow=True):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw   = bbox[2] - bbox[0]
        x    = (W - tw) // 2
        if shadow:
            draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 130))
        draw.text((x, y), text, font=font, fill=color)

    # ── LINE 1: "ZETTA × iiko" brand — top center ─────────────────────────────
    font_brand = _font(30, bold=False)
    centered_text("ZETTA × iiko", font_brand, y=48,
                  color=(255, 255, 255, 240), shadow=True)

    # thin separator line
    draw.line([(W // 2 - 55, 92), (W // 2 + 55, 92)],
              fill=(255, 255, 255, 180), width=1)

    # ── LINE 2: slogan — just below brand ─────────────────────────────────────
    slogan    = "Restoraningizni\naqlli boshqaring"
    font_slog = _font(52, bold=True)

    lines  = slogan.split("\n")
    line_h = draw.textbbox((0, 0), lines[0], font=font_slog)[3] + 10
    y_slog = 108   # just below the separator

    for i, line in enumerate(lines):
        centered_text(line, font_slog, y=y_slog + i * line_h,
                      color=(255, 255, 255, 255), shadow=True)

    # no bottom text at all
    return canvas.convert("RGB")


def main():
    photo_bytes = generate_bg()
    print("Compositing text …")
    poster = compose(photo_bytes)
    poster.save(OUT, format="JPEG", quality=95)
    print(f"Saved → {OUT}  ({poster.size[0]}×{poster.size[1]}px)")


if __name__ == "__main__":
    main()
