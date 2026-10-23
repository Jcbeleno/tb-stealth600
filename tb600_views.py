#!/usr/bin/env python3
"""Download the official Stealth 600 Gen 3 PC product shots from Turtle Beach's store into assets/views.

The photos are Turtle Beach's, so the repo does not carry them: install.sh runs this once. Each shot is
cropped to the headset, scaled down, and the two shots the store crops at the frame get a soft edge.
"""
import os, sys, urllib.request
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

CDN = "https://cdn.shopify.com/s/files/1/0556/5795/5430/files/"
# view: (store file, crop x, y, w, h of the headset in that file, edge to fade where the store cut it)
VIEWS = {
    "audio":       ("stealth-600-PC-BLK.png", 270, 49, 567, 742, None),
    "eq":          ("9-Stealth600-PC-BLK.png", 664, 163, 1072, 1187, "bottom"),
    "mic":         ("3-Stealth600-PC-BLK.png", 669, 102, 1063, 1146, None),
    "mic-muted":   ("2-Stealth600-PC-BLK.png", 958, 136, 485, 1078, None),
    "controls":    ("4-Stealth600-PC-BLK.png", 760, 0, 898, 1134, "top"),
    "device":      ("6-Stealth600-PC-BLK.png", 712, 136, 977, 1078, None),
    "profiles":    ("7-Stealth600-PC-BLK.png", 614, 384, 1172, 581, None),
    "audio-muted": ("5-Stealth600-PC-BLK.png", 712, 136, 977, 1078, None),
}
MAX = 1100
FADE = 0.18  # fraction of the height faded out at a cut edge


def fade(pb, edge):
    w, h, stride = pb.get_width(), pb.get_height(), pb.get_rowstride()
    px = bytearray(pb.get_pixels())
    n = int(h * FADE)
    for i in range(n):
        y = h - 1 - i if edge == "bottom" else i
        f = i / n
        row = px[y * stride:y * stride + w * 4]
        row[3::4] = bytes(int(a * f) for a in row[3::4])
        px[y * stride:y * stride + w * 4] = row
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(px)), GdkPixbuf.Colorspace.RGB,
                                           True, 8, w, h, stride)


def fetch(outdir):
    os.makedirs(outdir, exist_ok=True)
    for view, (name, x, y, w, h, edge) in VIEWS.items():
        dest = os.path.join(outdir, view + ".png")
        if os.path.exists(dest):
            continue
        req = urllib.request.Request(CDN + name, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=60).read()
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(data)
        loader.close()
        pb = loader.get_pixbuf().new_subpixbuf(x, y, w, h)
        s = min(1.0, MAX / max(w, h))
        pb = pb.scale_simple(round(w * s), round(h * s), GdkPixbuf.InterpType.HYPER)
        if edge:
            pb = fade(pb, edge)
        pb.savev(dest + ".tmp", "png", [], [])
        os.replace(dest + ".tmp", dest)
        print(f"{view}: {pb.get_width()}x{pb.get_height()}", flush=True)


if __name__ == "__main__":
    fetch(sys.argv[1] if len(sys.argv) > 1 else
          os.path.join(os.path.dirname(os.path.realpath(__file__)), "assets", "views"))
