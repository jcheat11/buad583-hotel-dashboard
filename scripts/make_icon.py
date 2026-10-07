"""
Builds the browser-tab icon (assets/torchfolio-icon.png) from the chart-and-flame symbol in the logo.

The symbol is black on transparent, which disappears on a dark browser theme, so it sits on a white
rounded square with a little padding, and the flame is drawn 1.5x larger so it still shows at 16 pixels.
Run: python scripts/make_icon.py
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "assets" / "torchfolio-portfolio-flame-black-orange.png"
ICON = ROOT / "assets" / "torchfolio-icon.png"
SYMBOL_BOX = (100, 100, 600, 620)      # the circle, bars and flame at the left of the logo (pixels)
SIZE, WORK = 64, 512                   # saved size; drawn large first, then reduced smoothly


def bigger_flame(symbol: Image.Image, factor: float) -> Image.Image:
    """The orange flame enlarged around its base, so it still reads at 16 pixels; the rest is unchanged."""
    pixels = np.array(symbol)
    r, g, b, a = (pixels[..., i].astype(int) for i in range(4))
    orange = (a > 0) & (r > 150) & (r - b > 90) & (g < r - 30)
    flame = np.zeros_like(pixels)
    flame[orange] = pixels[orange]
    rest = pixels.copy()
    rest[orange] = 0
    flame_image = Image.fromarray(flame)
    left, top, right, bottom = flame_image.getbbox()
    piece = flame_image.crop((left, top, right, bottom))
    piece = piece.resize((round(piece.width * factor), round(piece.height * factor)), Image.LANCZOS)
    base_x, base_y = (left + right) // 2, bottom                  # keep the flame sitting on the ring
    x, y = base_x - piece.width // 2, base_y - piece.height
    pad = max(0, -x, -y, x + piece.width - symbol.width)
    canvas = Image.new("RGBA", (symbol.width + 2 * pad, symbol.height + pad), (0, 0, 0, 0))
    canvas.alpha_composite(Image.fromarray(rest), (pad, pad))
    canvas.alpha_composite(piece, (x + pad, y + pad))
    return canvas.crop(canvas.getchannel("A").getbbox())


def build(padding: float = 0.03, flame: float = 1.5) -> Image.Image:
    logo = Image.open(LOGO).convert("RGBA")
    symbol = logo.crop(SYMBOL_BOX)
    symbol = bigger_flame(symbol.crop(symbol.getchannel("A").getbbox()), flame)
    tile = Image.new("RGBA", (WORK, WORK), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, WORK - 1, WORK - 1), radius=WORK // 5, fill="white")
    room = int(WORK * (1 - 2 * padding))
    scale = room / max(symbol.size)
    symbol = symbol.resize((round(symbol.width * scale), round(symbol.height * scale)), Image.LANCZOS)
    tile.alpha_composite(symbol, ((WORK - symbol.width) // 2, (WORK - symbol.height) // 2))
    return tile.resize((SIZE, SIZE), Image.LANCZOS)


if __name__ == "__main__":
    build().save(ICON)
    print(f"Wrote {ICON}")
