"""Genera l'icona dell'app (app/icon.png e AppIcon.icns)."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

S = 1024
out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")


def make():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # sfondo: quadrato arrotondato con sfumatura
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / S
        c = (int(18 + 10 * t), int(32 + 40 * t), int(64 + 50 * t), 255)
        gd.line([(0, y), (S, y)], fill=c)
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([60, 60, S - 60, S - 60], radius=200, fill=255)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)

    # candele
    candles = [(300, 560, 700, 600, 660, False), (420, 470, 650, 500, 610, True),
               (540, 400, 590, 430, 560, True), (660, 330, 520, 360, 470, True)]
    for x, hi, lo, top, bot, up in candles:
        col = (63, 185, 80, 255) if up else (248, 81, 73, 255)
        d.line([(x, hi - 40), (x, lo + 40)], fill=col, width=14)
        d.rounded_rectangle([x - 38, top, x + 38, bot], radius=10, fill=col)

    # freccia di copia (curva) bianca
    arrow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ad = ImageDraw.Draw(arrow)
    ad.arc([190, 230, 830, 870], start=200, end=330, fill=(255, 255, 255, 235), width=46)
    ad.polygon([(800, 300), (860, 430), (720, 410)], fill=(255, 255, 255, 235))
    img.alpha_composite(arrow)

    shadow = img.filter(ImageFilter.GaussianBlur(0))
    return shadow


icon = make()
out_dir.mkdir(parents=True, exist_ok=True)
icon.save(out_dir / "icon.png")
icon.save(out_dir / "AppIcon.icns", sizes=[(16, 16), (32, 32), (64, 64), (128, 128), (256, 256), (512, 512), (1024, 1024)])
print("ok")
