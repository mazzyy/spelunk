"""Generate packaging/icon.png and packaging/icon.icns (needs Pillow).

    python3 packaging/make_icon.py
"""
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
S = 2048  # draw at 2x, downsample for smooth edges


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(size, top, bottom):
    g = Image.new("RGB", (1, size))
    for y in range(size):
        g.putpixel((0, y), lerp(top, bottom, y / (size - 1)))
    return g.resize((size, size))


def main():
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    m = int(S * 100 / 1024)  # macOS icon grid: 824/1024 body
    body = (m, m, S - m, S - m)
    radius = int(S * 185 / 1024)

    # soft shadow
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((m, m + 24, S - m, S - m + 24), radius, fill=(0, 0, 0, 110))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(28)))

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(body, radius, fill=255)
    bg = gradient(S, (44, 49, 62), (16, 18, 24)).convert("RGBA")
    canvas.paste(bg, (0, 0), mask)

    # glow behind the peaks
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gx, gy, gr = S * 0.62, S * 0.40, S * 0.20
    ImageDraw.Draw(glow).ellipse((gx - gr, gy - gr, gx + gr, gy + gr), fill=(232, 176, 75, 90))
    glow = glow.filter(ImageFilter.GaussianBlur(int(S * 0.08)))
    glow.putalpha(Image.composite(glow.getchannel("A"), Image.new("L", (S, S), 0), mask))
    canvas = Image.alpha_composite(canvas, glow)

    # peaks (same shape as the favicon: M5 25 L12 11 L16 18 L20 8 L27 25 Z on a 32 grid)
    def pt(x, y):
        x0, y0, w = m, m, S - 2 * m
        return (x0 + (x - 2) / 28 * w, y0 + (y - 4) / 28 * w)

    peaks = [pt(5, 25), pt(12, 11), pt(16, 18), pt(20, 8), pt(27, 25)]
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    fill = gradient(S, (246, 196, 98), (201, 138, 27)).convert("RGBA")
    pmask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(pmask).polygon(peaks, fill=255)
    layer.paste(fill, (0, 0), pmask)
    canvas = Image.alpha_composite(canvas, layer)

    # ground line
    ImageDraw.Draw(canvas).rounded_rectangle(
        (pt(4, 25)[0], pt(4, 25)[1], pt(28, 26.2)[0], pt(28, 26.2)[1]), int(S * 0.01), fill=(232, 176, 75, 255))

    icon = canvas.resize((1024, 1024), Image.LANCZOS)
    icon.save(os.path.join(HERE, "icon.png"))
    icon.save(os.path.join(HERE, "icon.icns"))
    print("wrote icon.png and icon.icns")


if __name__ == "__main__":
    main()
