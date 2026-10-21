"""Download Beastmaster (XBM) game assets from XIVAPI and prepare them for the site.

Usage:  python tools/fetch_assets.py
Needs:  Pillow (pip install pillow)

Raw downloads are cached in tools/.cache so re-runs only redo the processing.
All game assets are (c) SQUARE ENIX and are used under the FFXIV Materials Usage License.
"""
import concurrent.futures as cf
import os
import sys
import urllib.request

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "tools", ".cache")
OUT = os.path.join(ROOT, "assets")
API = "https://v2.xivapi.com/api/asset?path={}&format=png"
UA = {"User-Agent": "bst-fansite-assets/1.0"}  # XIVAPI rejects Python's default UA

# Action and trait icons are fetched by tools/build_actions.py along with their tooltips.
BEAST_ICON_BASE = 242000  # 242001..242050 = beast portraits in bestiary order
JOB_ICON = 62143

ULD = [
    "xbmhudwindow", "xbmnotewindow", "xbmshopwindow",  # 9-slice prefixes
    "xbmmap", "xbmsymbol", "xbmnoteparts", "xbmmapparts", "xbmmapparts2",
    "xbmshopitemframe", "xbmactionlearned", "jobhudxbm0", "jobhudxbm1",
]
SLICES = ["tl", "tm", "tr", "ml", "mm", "mr", "bl", "bm", "br"]

# Sprite crops (x0, y0, x1, y1) in _hr1 pixel space, found by alpha-bounds detection
CROPS = {
    "xbmsymbol": {  # 128px grid
        "node-camp": (0, 0, 128, 128), "node-shop": (128, 0, 256, 128),
        "node-treasure": (256, 0, 384, 128), "node-random": (384, 0, 512, 128),
        "node-enemy": (0, 128, 128, 256), "node-elite": (128, 128, 256, 256),
        "node-boss": (256, 128, 384, 256),
    },
    "xbmnoteparts": {
        "row-header": (0, 296, 363, 340), "horn-1": (166, 224, 227, 292),
        "horn-2": (240, 224, 305, 291), "horn-3": (314, 224, 383, 290),
        "coin": (3, 203, 71, 274),
    },
    "xbmmapparts": {"node-start": (0, 0, 96, 96)},
    "xbmmapparts2": {"node-ring": (0, 0, 96, 96)},
    "xbmshopitemframe": {"chevron": (259, 3, 316, 33), "paw": (378, 2, 510, 128)},
    "xbmactionlearned": {"crest": (10, 11, 363, 362), "axe": (390, 12, 886, 473)},
    "jobhudxbm0": {
        "gauge-emblem": (2, 7, 514, 344), "bar-frame": (521, 285, 888, 335),
        "bar-empty": (0, 354, 328, 382), "bar-tp": (0, 384, 328, 412),
        "bar-ftp": (0, 414, 328, 442),
    },
    "jobhudxbm1": {  # affinity medallions (lit), 140px grid
        "affinity-eldritch": (0, 0, 140, 140), "affinity-volant": (140, 0, 280, 140),
        "affinity-durant": (0, 140, 140, 280), "affinity-rampant": (140, 140, 280, 280),
    },
}


def fetch(game_path):
    """Download one game texture as PNG, cached. Returns local path or None."""
    local = os.path.join(CACHE, game_path.replace("/", "_").replace(".tex", ".png"))
    if os.path.exists(local) and os.path.getsize(local) > 0:
        return local
    try:
        req = urllib.request.Request(API.format(game_path), headers=UA)
        data = urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        print(f"  ! {game_path}: {e}")
        return None
    with open(local, "wb") as f:
        f.write(data)
    return local


def icon_path(icon_id, hr=True):
    return f"ui/icon/{icon_id // 1000 * 1000:06d}/{icon_id:06d}{'_hr1' if hr else ''}.tex"


def uld_path(name):
    return f"ui/uld/{name}_hr1.tex"


def save(img, *parts):
    dest = os.path.join(OUT, *parts)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if dest.endswith(".jpg"):
        img.convert("RGB").save(dest, quality=85, optimize=True)
    else:
        img.save(dest, optimize=True)


def build_nine_slice(prefix):
    """Stitch the 9 window pieces into one image usable with CSS border-image."""
    t = {k: Image.open(os.path.join(CACHE, uld_path(prefix + k).replace("/", "_").replace(".tex", ".png"))).convert("RGBA")
         for k in SLICES}
    lw, mw, rw = t["tl"].width, t["tm"].width, t["tr"].width
    th, mh, bh = t["tl"].height, t["ml"].height, t["bl"].height
    out = Image.new("RGBA", (lw + mw + rw, th + mh + bh))
    for key, (x, y) in {
        "tl": (0, 0), "tm": (lw, 0), "tr": (lw + mw, 0),
        "ml": (0, th), "mm": (lw, th), "mr": (lw + mw, th),
        "bl": (0, th + mh), "bm": (lw, th + mh), "br": (lw + mw, th + mh),
    }.items():
        out.paste(t[key], (x, y))
    name = prefix.replace("xbm", "").replace("window", "")
    save(out, "ui", f"window-{name}.png")
    # slice values for CSS: top right bottom left
    print(f"  window-{name}.png  slice: {th} {rw} {bh} {lw}  size {out.size}")


def main():
    os.makedirs(CACHE, exist_ok=True)
    jobs = []
    for prefix in ULD[:3]:
        jobs += [uld_path(prefix + s) for s in SLICES]
    jobs += [uld_path(n) for n in ULD[3:]]
    jobs += [icon_path(BEAST_ICON_BASE + n) for n in range(1, 51)]
    jobs += [icon_path(JOB_ICON)]

    print(f"Fetching {len(jobs)} textures ...")
    with cf.ThreadPoolExecutor(12) as ex:
        missing = [p for p, r in zip(jobs, ex.map(fetch, jobs)) if r is None]
    if missing:
        sys.exit(f"Failed to fetch {len(missing)} textures; re-run to retry.")

    def cached(game_path):
        return Image.open(os.path.join(CACHE, game_path.replace("/", "_").replace(".tex", ".png"))).convert("RGBA")

    print("Windows")
    for prefix in ULD[:3]:
        build_nine_slice(prefix)

    print("Map + sprites")
    save(cached(uld_path("xbmmap")), "ui", "map.jpg")
    for sheet, crops in CROPS.items():
        src = cached(uld_path(sheet))
        for name, box in crops.items():
            save(src.crop(box), "ui", f"{name}.png")

    print("Icons")
    for n in range(1, 51):
        save(cached(icon_path(BEAST_ICON_BASE + n)), "icons", "beasts", f"{n:02d}.png")
    save(cached(icon_path(JOB_ICON)), "icons", "job-bst.png")
    print("Done ->", OUT)


if __name__ == "__main__":
    main()
