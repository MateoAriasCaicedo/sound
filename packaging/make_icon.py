"""Genera packaging/sound.ico: fondo oscuro redondeado con barras de ecualizador."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
SIZE = 256
BG = (16, 16, 19, 255)
BAR = (250, 250, 252, 255)
ACCENT = (255, 255, 255, 255)


def render() -> Image.Image:
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, SIZE - 8, SIZE - 8), radius=56, fill=BG)
    heights = [0.34, 0.58, 0.86, 0.52, 0.72, 0.40, 0.62]
    bar_w, gap = 20, 9
    total = len(heights) * bar_w + (len(heights) - 1) * gap
    x = (SIZE - total) // 2
    for height in heights:
        h = int(SIZE * height * 0.62)
        top = (SIZE - h) // 2
        color = ACCENT if height == max(heights) else BAR
        draw.rounded_rectangle((x, top, x + bar_w, top + h), radius=bar_w // 2, fill=color)
        x += bar_w + gap
    return image


def main() -> Path:
    image = render()
    target = ROOT / "sound.ico"
    image.save(target, format="ICO",
               sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    png = ROOT / "sound.png"
    image.save(png, format="PNG")
    return target


if __name__ == "__main__":
    print(main())
