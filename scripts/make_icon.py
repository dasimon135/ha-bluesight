"""Generate the BlueSight brand icon (an original connection-slot motif).

Home Assistant (>= 2026.3) loads a custom integration's brand images from the
``custom_components/<domain>/brand/`` directory, taking priority over the
``home-assistant/brands`` CDN -- no PR to that repository is needed:

- ``icon.png`` (256x256) / ``icon@2x.png`` (512x512) -- the square icon.
- ``logo.png`` / ``logo@2x.png`` -- the integration-page logo (a copy of the
  square icon; the project has no separate wordmark).

The motif is deliberately original and does NOT use the Bluetooth trademark or
figure mark. It reads as "connection-slot triage": a central proxy hub with a
ring of GATT slot pips around it -- some lit (allocated), some dim (free) --
plus a soft diagnostic pulse. Green/amber lit slots evoke a health readout.

Run with a Python that has Pillow::

    python scripts/make_icon.py

Outputs are written under ``custom_components/bluesight/brand/``.
"""

from __future__ import annotations

import math
import os

from PIL import Image, ImageDraw, ImageFilter

# Supersample everything, then downscale with LANCZOS for crisp anti-aliasing.
SS = 4
OUT = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "custom_components", "bluesight", "brand",
)

# Palette: a deep slate/teal field (diagnostic tooling) with health-coded pips.
BG_TOP = (16, 34, 52)       # deep slate-blue
BG_BOTTOM = (13, 59, 74)    # dark teal
HUB = (226, 244, 255)       # cool white hub
SLOT_USED = (94, 234, 168)  # healthy green (allocated slot)
SLOT_FREE = (86, 108, 133)  # muted slate (free slot)
SLOT_ALERT = (255, 191, 92) # amber (one slot flagged -> triage)
EDGE = (150, 210, 235)      # spokes (drawn semi-transparent)


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def _gradient(size: int) -> Image.Image:
    grad = Image.new("RGB", (size, size), BG_TOP)
    px = grad.load()
    for y in range(size):
        t = y / (size - 1)
        r = round(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
        g = round(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
        b = round(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
        for x in range(size):
            px[x, y] = (r, g, b)
    return grad


def _glow(size: int, cx: float, cy: float, radius: float, color) -> Image.Image:
    """A soft radial glow as its own RGBA layer (blurred filled circle)."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=color + (255,),
    )
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.55))


def build(size_out: int) -> Image.Image:
    S = size_out * SS
    # Rounded-rect gradient background.
    bg = _gradient(S)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(bg, (0, 0), _rounded_mask(S, int(S * 0.22)))

    cx = cy = S / 2
    ring = S * 0.30          # radius of the slot ring
    slot_r = S * 0.056       # slot pip radius
    hub_r = S * 0.088        # central hub radius

    # Slot pips: 8 around the ring. Mostly used (green), a couple free (slate),
    # one flagged (amber) -> the "triage" read.
    n = 8
    # Health per slot: "used", "free", or "alert".
    health = [
        "used", "used", "free", "used",
        "alert", "used", "free", "used",
    ]
    slots = [
        (
            cx + ring * math.cos(-math.pi / 2 + i * 2 * math.pi / n),
            cy + ring * math.sin(-math.pi / 2 + i * 2 * math.pi / n),
        )
        for i in range(n)
    ]

    # Spokes: hub -> each slot (a star/hub topology).
    edges = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ed = ImageDraw.Draw(edges)
    lw = max(1, int(S * 0.010))
    for sx, sy in slots:
        ed.line([cx, cy, sx, sy], fill=EDGE + (70,), width=lw)
    img = Image.alpha_composite(img, edges)

    # Central hub glow, then the slot pips, then the hub.
    img = Image.alpha_composite(img, _glow(S, cx, cy, hub_r * 2.2, HUB))

    # Glow the lit (used/alert) slots so they pop as "active".
    for (sx, sy), h in zip(slots, health):
        if h == "used":
            img = Image.alpha_composite(img, _glow(S, sx, sy, slot_r * 1.9, SLOT_USED))
        elif h == "alert":
            img = Image.alpha_composite(img, _glow(S, sx, sy, slot_r * 2.1, SLOT_ALERT))

    d = ImageDraw.Draw(img)
    for (sx, sy), h in zip(slots, health):
        color = {"used": SLOT_USED, "free": SLOT_FREE, "alert": SLOT_ALERT}[h]
        d.ellipse(
            [sx - slot_r, sy - slot_r, sx + slot_r, sy + slot_r],
            fill=color + (255,),
        )
    d.ellipse(
        [cx - hub_r, cy - hub_r, cx + hub_r, cy + hub_r],
        fill=HUB + (255,),
    )

    return img.resize((size_out, size_out), Image.LANCZOS)


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    icon = build(256)
    icon_2x = build(512)
    icon.save(os.path.join(OUT, "icon.png"))
    icon_2x.save(os.path.join(OUT, "icon@2x.png"))
    # The integration page logo is the same square mark (no separate wordmark).
    icon.save(os.path.join(OUT, "logo.png"))
    icon_2x.save(os.path.join(OUT, "logo@2x.png"))
    print("wrote icon.png/icon@2x.png/logo.png/logo@2x.png to", OUT)


if __name__ == "__main__":
    main()
