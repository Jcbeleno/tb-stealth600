#!/usr/bin/env python3
"""TB Stealth 600: Linux replacement for Turtle Beach Swarm II (Stealth 600 Gen 3 PC).

Look follows Swarm II: palette and font come from its stylesheets (#12151A background, #202731 cards,
#4DFCE4 accent, Noto Sans, uppercase titles), side navigation with the headset picture, cards per section
and an EQ curve with the preset list beside it.
"""
import json, math, os, queue, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk
from tb600 import (EQ_LIMIT, GAME_EQ_PRESETS, MIC_EQ_PRESETS, SETTINGS, Headset)
import tb600d

APP_ID = "dev.jucabego.TBStealth600"
HERE = os.path.dirname(os.path.realpath(__file__))
PROFILES = os.path.expanduser("~/.config/tb600/profiles")
BACKUPS = os.path.expanduser("~/.config/tb600/backups")  # tbctl backup/restore format
# shortcut: band centre frequencies are the usual 10-band ISO set; Swarm's own labels are images
BAND_HZ = ["32", "64", "125", "250", "500", "1k", "2k", "4k", "8k", "16k"]
REFRESH_MS = 300  # tb600d pushes headset changes into state.json within ~0.1 s
HOLD_S = 4  # keep a just-edited control from being overwritten until the headset's push arrives
LOCAL_KEYS = ("surround", "surround_preset", "route_default", "battery_alerts")
ACCENT = (0x4D / 255, 0xFC / 255, 0xE4 / 255)

CSS = """
@define-color accent_bg_color #4dfce4;
@define-color accent_fg_color #0c1f1d;
@define-color accent_color #4dfce4;
@define-color window_bg_color #12151a;
@define-color view_bg_color #12151a;
@define-color headerbar_bg_color #12151a;
@define-color card_bg_color #202731;
@define-color popover_bg_color #202731;
@define-color dialog_bg_color #202731;
@define-color destructive_bg_color #ff4483;
window { font-family: "Noto Sans"; }
.tb-sidebar { background: #0d1014; border-right: 1px solid rgba(255,255,255,0.06); }
.tb-device { background: radial-gradient(circle, rgba(255,255,255,0.16), rgba(255,255,255,0.0) 70%); }
.tb-device-name { font-weight: 700; letter-spacing: 1px; }
.tb-nav { background: rgba(255,255,255,0.03); border-radius: 999px; padding: 4px; }
.tb-nav > button { border-radius: 999px; padding: 6px 14px; color: rgba(255,255,255,0.6);
                   transition: background 200ms ease-out, color 200ms ease-out; }
.tb-nav > button:checked { background: rgba(77,252,228,0.14); color: #ffffff; }
.tb-nav > button:checked image { color: #4dfce4; }
.tb-nav > button label { letter-spacing: 1px; font-size: 12px; font-weight: 600; }
.tb-nav row { padding: 11px 18px; border-left: 3px solid transparent; color: rgba(255,255,255,0.6); }
.tb-nav row:hover { background: rgba(255,255,255,0.04); }
.tb-nav row:selected { background: rgba(77,252,228,0.08); color: #ffffff; border-left-color: #4dfce4; }
.tb-nav row label { letter-spacing: 1px; font-size: 12px; font-weight: 600; }
.tb-title { font-size: 26px; font-weight: 700; letter-spacing: 2px; }
.tb-title-light { font-size: 26px; font-weight: 300; letter-spacing: 2px; color: rgba(255,255,255,0.55); }
.tb-subtitle { font-size: 12px; letter-spacing: 2px; color: rgba(255,255,255,0.6); }
.tb-status { color: rgba(255,255,255,0.8); }
.tb-dot-ok { color: #3ddc84; }
.tb-dot-warn { color: #ffc14d; }
.tb-dot-bad { color: #ff4483; }
.tb-card { background: #202731; border-radius: 6px; padding: 16px 20px 12px 20px; }
.tb-card-title { font-size: 15px; font-weight: 600; letter-spacing: 1.5px; margin-bottom: 4px; }
.tb-card-text { color: rgba(255,255,255,0.55); font-size: 12px; }
.tb-list { background: transparent; }
.tb-list > row { background: transparent; border: none; }
.tb-list > row:hover { background: rgba(255,255,255,0.03); }
.tb-list row .title { letter-spacing: 1px; font-size: 12.5px; color: rgba(255,255,255,0.85); }
.tb-list row .subtitle { color: rgba(255,255,255,0.5); }
scale trough { background: rgba(255,255,255,0.14); min-height: 4px; }
scale highlight { background: #4dfce4; }
scale slider { background: #ffffff; min-width: 16px; min-height: 16px; }
.tb-preset-head { font-size: 11px; letter-spacing: 1.5px; color: rgba(255,255,255,0.5); margin-top: 8px; }
.tb-presets { background: transparent; }
.tb-presets > row { background: transparent; padding: 6px 4px; }
.tb-list > row .title { letter-spacing: 1px; font-size: 12.5px; color: rgba(255,255,255,0.85); }
.tb-presets > row label { letter-spacing: 1px; font-size: 13px; }
.tb-preset-active { color: #4dfce4; }
.tb-list > row, .tb-presets, drawingarea { transition: opacity 250ms ease-out; }
.tb-list > row:disabled, .tb-presets:disabled, drawingarea:disabled { opacity: 0.35; }
.tb-mute-notice { background: rgba(255,68,131,0.10); border-radius: 6px; padding: 8px 12px; margin: 4px 0 6px 0;
                  color: #ff8fb4; font-size: 12px; }
.tb-outline { border: 1px solid rgba(255,255,255,0.35); background: transparent; border-radius: 18px; padding: 6px 18px; }
.tb-outline:hover { border-color: #4dfce4; color: #4dfce4; }
"""


class Worker(threading.Thread):
    """Owns the Headset; runs jobs off the UI thread and reconnects on failure."""

    def __init__(self, on_error):
        super().__init__(daemon=True)
        self.q, self.h, self.on_error = queue.Queue(), None, on_error

    def run(self):
        while True:
            fn, done = self.q.get()
            try:
                self.h = self.h or Headset()
                res = fn(self.h)
                if done:
                    GLib.idle_add(done, res)
            except Exception as e:  # any failure: drop the handle, show it, keep the thread alive
                if self.h:
                    self.h.close()
                self.h = None
                GLib.idle_add(self.on_error, str(e))

    def do(self, fn, done=None):
        self.q.put((fn, done))


def save_config(cfg):
    os.makedirs(os.path.dirname(tb600d.CONFIG), exist_ok=True)
    json.dump(cfg, open(tb600d.CONFIG, "w"), indent=1)


class HeadsetStage(Gtk.Widget):
    """The headset as the app's centre: one official product shot per section (earcup driver for the EQ,
    the boom for the mic, the buttons for the controls...). Changing section turns the headset: the old
    shot swings away and the new one swings in from the side of the tab you came from, so the photos read
    as one object moving. Mic open/muted swaps to a shot with the boom down/up. Grey stand-by when off,
    light sweep on power-on."""

    ORDER = ["audio", "eq", "mic", "controls", "device", "profiles"]
    MUTED_VIEW = {"audio": "audio-muted", "mic": "mic-muted"}  # boom flipped up = muted
    TURN = 28  # degrees of the simulated turn between shots

    def __init__(self, folder):
        super().__init__()
        self.tex = {os.path.splitext(f)[0]: Gdk.Texture.new_from_filename(os.path.join(folder, f))
                    for f in os.listdir(folder) if f.endswith(".png")}
        self.page, self.muted, self.link = "audio", False, None
        self.cur, self.prev, self.dir, self.t = "audio", None, 0, 1.0
        self.dim, self.sweep, self.breath = 1.0, -1.0, 0.0
        self._anim_turn = self._anim(650, Adw.Easing.EASE_OUT_EXPO, lambda v: self._set("t", v))
        self._anim_dim = self._anim(600, Adw.Easing.EASE_OUT_CUBIC, lambda v: self._set("dim", v))
        self._anim_sweep = self._anim(900, Adw.Easing.EASE_IN_OUT_CUBIC, lambda v: self._set("sweep", v))
        self._anim_breath = self._anim(2400, Adw.Easing.EASE_IN_OUT_SINE, lambda v: self._set("breath", v))
        self._anim_breath.set_repeat_count(0)  # infinite, but only while off; GTK stops ticking when hidden
        self._anim_breath.set_alternate(True)

    def _anim(self, ms, easing, cb):
        a = Adw.TimedAnimation(widget=self, value_from=0, value_to=1, duration=ms,
                               target=Adw.CallbackAnimationTarget.new(cb))
        a.set_easing(easing)
        return a

    def _set(self, k, v):
        setattr(self, k, v)
        self.queue_draw()

    def _show(self, direction):
        view = self.MUTED_VIEW.get(self.page, self.page) if self.muted else self.page
        if view == self.cur:
            return
        self.prev, self.cur, self.dir = self.cur, view, direction
        self._anim_turn.reset()
        self._anim_turn.play()

    # --- state API ---
    def focus(self, page):
        if page == self.page:
            return
        a, b = self.ORDER.index(self.page), self.ORDER.index(page)
        self.page = page
        self._show(1 if b > a else -1)

    def set_muted(self, muted):
        if muted != self.muted:
            self.muted = muted
            self._show(0)  # same angle family: a short dissolve while the boom moves

    def set_link(self, up):
        if up == self.link:
            return
        first, self.link = self.link is None, up
        self._anim_dim.set_value_from(self.dim if not first else (1.0 if up else 0.0))
        self._anim_dim.set_value_to(0.0 if up else 1.0)
        self._anim_dim.reset()
        self._anim_dim.play()
        self._anim_breath.reset()
        if up:  # power-on: light sweeps across the silhouette while the colour comes back
            self._anim_sweep.reset()
            self._anim_sweep.play()
        else:
            self._anim_breath.play()

    # --- drawing ---
    def _rect(self, key, w, h):
        tex = self.tex[key]
        iw, ih = tex.get_width(), tex.get_height()
        sc = min(w * 0.86 / iw, h * 0.80 / ih)
        return Graphene.Rect().init(-iw * sc / 2, -ih * sc / 2, iw * sc, ih * sc)

    def _draw(self, s, key, w, h, shift, angle, scale, opacity):
        if opacity <= 0.01:
            return
        s.save()
        s.translate(Graphene.Point().init(w / 2 + shift, h / 2))
        s.perspective(1400)
        s.rotate_3d(angle, Graphene.Vec3.y_axis())
        s.scale(scale, scale)
        rect = self._rect(key, w, h)
        s.push_opacity(opacity * (1 - 0.62 * self.dim - 0.12 * self.dim * self.breath))
        s.push_color_matrix(self._saturation(1 - self.dim), Graphene.Vec4().init(0, 0, 0, 0))
        s.append_texture(self.tex[key], rect)
        s.pop()
        s.pop()
        if 0 < self.sweep < 1 and key == self.cur:  # light band clipped to the headset's own alpha
            s.push_mask(Gsk.MaskMode.ALPHA)
            s.append_texture(self.tex[key], rect)
            s.pop()
            x, y, rw, rh = rect.get_x(), rect.get_y(), rect.get_width(), rect.get_height()
            band = x - 0.4 * rw + self.sweep * 1.8 * rw
            s.append_linear_gradient(rect, Graphene.Point().init(band - 0.15 * rw, y),
                                     Graphene.Point().init(band + 0.15 * rw, y + 0.1 * rh),
                                     [self._stop(0, (*ACCENT, 0)), self._stop(0.5, (*ACCENT, 0.55)),
                                      self._stop(1, (*ACCENT, 0))])
            s.pop()
        s.restore()

    def do_snapshot(self, s):
        w, h = self.get_width(), self.get_height()
        self._radial(s, w / 2, h / 2, min(w, h) * 0.5, (1, 1, 1, 0.10 * (1 - self.dim)))
        t, d = self.t, self.dir
        travel = 0.22 * w
        if self.prev and t < 1:
            # the old shot swings out towards the side we are leaving, the new one swings in from the other
            self._draw(s, self.prev, w, h, -d * travel * t, -d * self.TURN * t, 1 - 0.06 * t, 1 - t)
            self._draw(s, self.cur, w, h, d * travel * (1 - t), d * self.TURN * (1 - t), 0.94 + 0.06 * t, t)
        else:
            self._draw(s, self.cur, w, h, 0, 0, 1, 1)

    @staticmethod
    def _rgba(c):
        g = Gdk.RGBA()
        g.red, g.green, g.blue, g.alpha = c
        return g

    def _stop(self, off, c):
        st = Gsk.ColorStop()
        st.offset, st.color = off, self._rgba(c)
        return st

    def _radial(self, s, x, y, r, c):
        if c[3] <= 0.001:
            return
        b = Graphene.Rect().init(x - r, y - r, 2 * r, 2 * r)
        s.append_radial_gradient(b, Graphene.Point().init(x, y), r, r, 0, 1,
                                 [self._stop(0, c), self._stop(1, (*c[:3], 0))])

    @staticmethod
    def _saturation(sat):
        # Rec. 709 luma weights; sat=1 identity, sat=0 greyscale
        lr, lg, lb = 0.2126, 0.7152, 0.0722
        m = [lr * (1 - sat) + sat, lr * (1 - sat), lr * (1 - sat), 0,
             lg * (1 - sat), lg * (1 - sat) + sat, lg * (1 - sat), 0,
             lb * (1 - sat), lb * (1 - sat), lb * (1 - sat) + sat, 0,
             0, 0, 0, 1]
        return Graphene.Matrix().init_from_float(m)


class EqGraph(Gtk.DrawingArea):
    """Swarm-style EQ: 10 draggable points on a curve, ±9 dB in 0.5 dB steps (scroll also works)."""

    class Band:
        def __init__(self, graph):
            self.graph, self.v = graph, 0.0

        def get_value(self):
            return self.v

        def set_value(self, v):
            v = max(-EQ_LIMIT / 10, min(EQ_LIMIT / 10, round(v * 2) / 2))
            if v != self.v:
                self.v = v
                self.graph.queue_draw()
                self.graph.on_change()

    PAD_L, PAD_R, PAD_T, PAD_B = 52, 18, 14, 30

    def __init__(self, on_change):
        super().__init__(hexpand=True, content_height=300)
        self.on_change = on_change
        self.bands = [EqGraph.Band(self) for _ in BAND_HZ]
        self.drag_band = None
        self.set_draw_func(self.draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-update", self.on_update)
        self.add_controller(drag)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self.on_scroll)
        self.add_controller(scroll)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: setattr(self, "pointer_x", x))
        self.add_controller(motion)

    def geom(self):
        w, h = self.get_width(), self.get_height()
        return self.PAD_L, self.PAD_T, w - self.PAD_L - self.PAD_R, h - self.PAD_T - self.PAD_B

    def xy(self, i, v):
        x0, y0, w, h = self.geom()
        lim = EQ_LIMIT / 10
        return x0 + w * (i + 0.5) / len(self.bands), y0 + h * (lim - v) / (2 * lim)

    def band_at(self, x):
        x0, _, w, _ = self.geom()
        return max(0, min(len(self.bands) - 1, int((x - x0) / w * len(self.bands))))

    def value_at(self, y):
        _, y0, _, h = self.geom()
        lim = EQ_LIMIT / 10
        return lim - (y - y0) / h * 2 * lim

    def on_begin(self, g, x, y):
        self.drag_band = self.band_at(x)
        self.drag_y0 = y
        self.bands[self.drag_band].set_value(self.value_at(y))

    def on_update(self, g, dx, dy):
        if self.drag_band is not None:
            self.bands[self.drag_band].set_value(self.value_at(self.drag_y0 + dy))

    def on_scroll(self, c, dx, dy):
        b = self.bands[self.band_at(getattr(self, "pointer_x", 0))]
        b.set_value(b.get_value() - 0.5 * (1 if dy > 0 else -1))
        return True

    def draw(self, area, cr, w, h):
        x0, y0, gw, gh = self.geom()
        lim = EQ_LIMIT / 10
        cr.select_font_face("Noto Sans")
        cr.set_font_size(11)
        for db in (lim, lim / 2, 0, -lim / 2, -lim):  # horizontal guides + dB labels
            _, y = self.xy(0, db)
            cr.set_source_rgba(1, 1, 1, 0.16 if db == 0 else 0.07)
            cr.set_line_width(1)
            cr.move_to(x0, y + 0.5)
            cr.line_to(x0 + gw, y + 0.5)
            cr.stroke()
            label = f"{db:+g} dB" if db else "0 dB"
            ext = cr.text_extents(label)
            cr.set_source_rgba(1, 1, 1, 0.5)
            cr.move_to(x0 - 10 - ext.width, y + ext.height / 2)
            cr.show_text(label)
        pts = [self.xy(i, b.v) for i, b in enumerate(self.bands)]
        for i, (x, _) in enumerate(pts):  # band columns + frequency labels
            cr.set_source_rgba(1, 1, 1, 0.08)
            cr.move_to(x + 0.5, y0)
            cr.line_to(x + 0.5, y0 + gh)
            cr.stroke()
            label = BAND_HZ[i]
            ext = cr.text_extents(label)
            cr.set_source_rgba(1, 1, 1, 0.6)
            cr.move_to(x - ext.width / 2, y0 + gh + 20)
            cr.show_text(label)
        # filled area under the curve, then the curve (Catmull-Rom through the points)
        _, yz = self.xy(0, 0)
        path = [pts[0]] + pts + [pts[-1]]

        def curve():
            cr.move_to(*pts[0])
            for i in range(1, len(path) - 2):
                p0, p1, p2, p3 = path[i - 1], path[i], path[i + 1], path[i + 2]
                cr.curve_to(p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6,
                            p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6, p2[0], p2[1])
        curve()
        cr.line_to(pts[-1][0], yz)
        cr.line_to(pts[0][0], yz)
        cr.close_path()
        cr.set_source_rgba(*ACCENT, 0.12)
        cr.fill()
        curve()
        cr.set_source_rgba(*ACCENT, 1)
        cr.set_line_width(2.5)
        cr.stroke()
        for i, (x, y) in enumerate(pts):  # handles + value of the dragged band
            cr.arc(x, y, 7, 0, 2 * math.pi)
            cr.set_source_rgb(1, 1, 1)
            cr.fill()
            if i == self.drag_band or self.bands[i].v:
                txt = f"{self.bands[i].v:+.1f}"
                ext = cr.text_extents(txt)
                cr.set_source_rgba(*ACCENT, 1 if i == self.drag_band else 0.7)
                cr.move_to(x - ext.width / 2, y - 13)
                cr.show_text(txt)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="TB Stealth 600", default_width=1360, default_height=820)
        self.worker = Worker(self.show_error)
        self.worker.start()
        self.widgets, self.busy_until, self.pending, self.known = {}, {}, {}, {}
        self.cfg = tb600d.load_config()
        self.presets = {}

        # --- the headset is the centre of the app; each section's settings open out on both sides of it ---
        self.stage = HeadsetStage(os.path.join(HERE, "assets", "views"))
        self.stage.set_vexpand(True)
        # wings slide in tab order: a later section comes in from the right, an earlier one from the left
        self.left = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, transition_duration=350, hexpand=True)
        self.right = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, transition_duration=350, hexpand=True)
        wings = Gtk.Box(vexpand=True, homogeneous=True)  # three equal columns keep the headset dead centre
        for w in (self.left, self.stage, self.right):
            wings.append(w)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        header = Adw.HeaderBar(css_classes=["flat"], title_widget=Gtk.Label())
        main.append(header)
        top = Gtk.Box(spacing=12, margin_start=32, margin_end=32)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        name = Gtk.Box(spacing=10)
        name.append(Gtk.Label(label="STEALTH 600", css_classes=["tb-title"]))
        name.append(Gtk.Label(label="GEN 3 PC", css_classes=["tb-title-light"]))
        titles.append(name)
        self.page_title = Gtk.Label(xalign=0, css_classes=["tb-subtitle"])
        titles.append(self.page_title)
        top.append(titles)
        self.status_bat = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.bat_icon = Gtk.Image(icon_name="battery-missing-symbolic")
        self.bat_label = Gtk.Label(css_classes=["tb-status"])
        self.status_bat.append(self.bat_icon)
        self.status_bat.append(self.bat_label)
        top.append(self.status_bat)
        top.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=12, margin_end=12))
        status = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.dot = Gtk.Label(label="●", css_classes=["tb-dot-warn"])
        self.status_label = Gtk.Label(label="Conectando…", css_classes=["tb-status"])
        status.append(self.dot)
        status.append(self.status_label)
        top.append(status)
        main.append(top)
        self.nav = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, margin_top=14, css_classes=["tb-nav"])
        main.append(self.nav)
        self.banner = Adw.Banner(revealed=False, margin_top=8)
        main.append(self.banner)
        main.append(wings)
        self.toasts = Adw.ToastOverlay(child=main)
        self.set_content(self.toasts)

        self.add_page("audio", "Audio", "audio-headphones-symbolic", [
            [("Mezcla", ["chat_mix", "chat_boost", "route_default"]),
             ("Sonido envolvente", ["surround", "surround_preset"])],
            [("Superhuman Hearing", ["shh", "shh_preset", "shh_level"])],
        ])
        self.add_eq_page()
        self.add_page("mic", "Micrófono", "audio-input-microphone-symbolic", [
            [("Micrófono", ["mic_mute", "mic_sensitivity", "mic_monitor"])],
            [("Noise gate", ["noise_gate", "noise_gate_level"])],
        ])
        self.add_page("controls", "Controles", "input-gaming-symbolic", [
            [("PC", ["button", "wheel"])],
            [("Xbox", ["button_xbox", "wheel_xbox"])],
        ])
        self.add_page("device", "Dispositivo", "preferences-system-symbolic", [
            [("Estado", self.info_list()), ("Mantenimiento", self.maintenance_list())],
            [("Energía y avisos", ["auto_shutoff", "battery_alerts"]),
             ("Transmisor y avisos de voz", ["led_brightness", "prompts"]), ("General", ["headset_name"])],
        ])
        self.add_profiles_page()
        self.nav.get_first_child().set_active(True)

        self.set_wings_sensitive(False)  # placeholders must not be written back before the first read
        self.in_flight = False
        self.refresh()
        GLib.timeout_add(REFRESH_MS, self.refresh)

    # --- building blocks -----------------------------------------------
    def nav_row(self, name, title, icon):
        b = Gtk.Box(spacing=8)
        b.append(Gtk.Image(icon_name=icon))
        b.append(Gtk.Label(label=title.upper()))
        r = Gtk.ToggleButton(child=b, group=self.nav.get_first_child(), css_classes=["flat"])
        r.page, r.title = name, title
        r.connect("toggled", lambda w: w.get_active() and self.show_page(w.page, w.title))
        self.nav.append(r)

    def show_page(self, name, title):
        self.left.set_visible_child_name(name)
        self.right.set_visible_child_name(name)
        self.page_title.set_label(title.upper())
        self.focus_stage(name)

    def add_wings(self, name, left, right):
        for stack, child in ((self.left, left), (self.right, right)):
            child.set_valign(Gtk.Align.CENTER)  # short sections sit level with the headset
            stack.add_named(self.page_scroller(child), name)

    def set_wings_sensitive(self, on):
        self.left.set_sensitive(on)
        self.right.set_sensitive(on)

    def card(self, title, content, text=None):
        c = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["tb-card"], spacing=4)
        c.append(Gtk.Label(label=title.upper(), xalign=0, css_classes=["tb-card-title"]))
        if title == "Micrófono":
            self.mic_notice = self.mute_notice()
            c.append(self.mic_notice)
        if text:
            c.append(Gtk.Label(label=text, xalign=0, wrap=True, css_classes=["tb-card-text"]))
        if isinstance(content, list):
            lst = Gtk.ListBox(css_classes=["tb-list"], selection_mode=Gtk.SelectionMode.NONE)
            for n in content:
                lst.append(self.row(n))
            content = lst
        c.append(content)
        return c

    def page_scroller(self, child):
        sc = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        child.set_margin_start(28)
        child.set_margin_end(28)
        sc.set_child(child)
        return sc

    def add_page(self, name, title, icon, columns):
        boxes = []
        for col in columns:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=16, margin_bottom=32)
            for spec in col:
                box.append(self.card(*spec))
            boxes.append(box)
        self.add_wings(name, *boxes)
        self.nav_row(name, title, icon)

    def row(self, name):
        if name == "surround":
            r = Adw.SwitchRow(title="SONIDO ENVOLVENTE (3D)",
                              active=self.cfg["surround"])
            r.connect("notify::active", lambda w, _: self.set_local("surround", w.get_active()))
            return r
        if name == "battery_alerts":
            r = Adw.SwitchRow(title="AVISOS DE BATERÍA BAJA", subtitle="15% y 5%",
                              active=self.cfg.get("battery_alerts", True))
            r.connect("notify::active", lambda w, _: self.set_local("battery_alerts", w.get_active()))
            return r
        if name == "surround_preset":
            keys, names = ["game", "movie", "music"], ["Juegos", "Películas", "Música"]
            r = Adw.ComboRow(title="TIPO", model=Gtk.StringList.new(names),
                             selected=keys.index(self.cfg.get("surround_preset", "game")))
            r.connect("notify::selected", lambda w, _: self.set_local("surround_preset", keys[w.get_selected()]))
            return r
        if name == "route_default":
            r = Adw.SwitchRow(title="«TB GAME» COMO SALIDA PREDETERMINADA",
                              active=self.cfg["route_default"])
            r.connect("notify::active", lambda w, _: self.set_local("route_default", w.get_active()))
            return r
        s = SETTINGS[name]
        title = {"button": "Función del botón", "button_xbox": "Función del botón",
                 "wheel": "Rueda configurable", "wheel_xbox": "Rueda configurable",
                 "shh_preset": "Modo", "shh_level": "Nivel", "noise_gate_level": "Nivel",
                 "shh": "Superhuman Hearing", "mic_monitor": "Monitorización", "mic_sensitivity": "Sensibilidad"}.get(name, s.label).upper()
        if s.kind == "bool":
            r = Adw.SwitchRow(title=title)
            r.connect("notify::active", lambda w, _: self.user_set(name, w.get_active()))
        elif s.kind == "int":
            r = Gtk.ListBoxRow(activatable=False)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=12, margin_end=12,
                          margin_top=10, margin_bottom=6)
            box.append(Gtk.Label(label=title, xalign=0, css_classes=["title"]))
            sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, s.lo, s.hi, 1)
            sc.set_hexpand(True)
            sc.set_draw_value(True)
            sc.set_value_pos(Gtk.PositionType.RIGHT)
            sc.set_digits(0)
            sc.connect("value-changed", lambda w: self.user_set(name, int(w.get_value()), debounce=True))
            if name == "chat_mix":
                sc.add_mark(50, Gtk.PositionType.BOTTOM, None)
            box.append(sc)
            r.set_child(box)
            r.scale = sc
        elif s.kind == "choice":
            items = list(s.choices.values()) if isinstance(s.choices, dict) else list(s.choices)
            r = Adw.ComboRow(title=title, model=Gtk.StringList.new(items))
            keys = list(s.choices) if isinstance(s.choices, dict) else list(range(len(items)))
            r.keys = keys
            r.connect("notify::selected", lambda w, _: self.user_set(name, keys[w.get_selected()]))
        else:
            r = Adw.EntryRow(title=title, show_apply_button=True)
            r.connect("apply", lambda w: self.user_set(name, w.get_text()))
        self.widgets[name] = r
        return r

    # --- equalizer page (Swarm: curve card + presets card) ---
    def add_eq_page(self):
        graphs = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        lists = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.mic_parts = []  # greyed out while the mic is muted (nothing to hear while adjusting)
        for which in ("game", "mic"):
            g = EqGraph(lambda wh=which: self.eq_changed(wh))
            self.widgets[f"eq_{which}"] = g.bands
            graphs.add_named(g, which)
            pl = self.preset_list(which)
            lists.add_named(pl, which)
            if which == "mic":
                self.mic_parts += [g, pl]
        tabs = Adw.ToggleGroup(halign=Gtk.Align.CENTER, css_classes=["round"])
        tabs.add(Adw.Toggle(name="game", label="SONIDO"))
        tabs.add(Adw.Toggle(name="mic", label="MICRÓFONO"))
        tabs.connect("notify::active-name", lambda t, _: (graphs.set_visible_child_name(t.get_active_name()),
                                                         lists.set_visible_child_name(t.get_active_name()),
                                                         self.focus_stage(self.left.get_visible_child_name()),
                                                         self.show_eq_notice()))
        self.eq_tabs = tabs
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["tb-card"], hexpand=True,
                       valign=Gtk.Align.START)
        left.append(tabs)
        self.mic_eq_notice = self.mute_notice()
        left.append(self.mic_eq_notice)
        left.append(graphs)
        buttons = Gtk.Box(spacing=12)
        flat = Gtk.Button(label="Plano", css_classes=["tb-outline"])
        flat.connect("clicked", lambda *_: [b.set_value(0) for b in self.widgets[f"eq_{tabs.get_active_name()}"]])
        save = Gtk.Button(label="Guardar como nuevo preset", css_classes=["tb-outline"], halign=Gtk.Align.END, hexpand=True)
        save.connect("clicked", lambda *_: self.ask_preset_name(tabs.get_active_name()))
        buttons.append(flat)
        buttons.append(save)
        left.append(buttons)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["tb-card"])
        right.append(Gtk.Label(label="PRESETS", xalign=0, css_classes=["tb-card-title"]))
        right.append(lists)
        self.add_wings("eq", left, right)
        self.nav_row("eq", "Ecualizador", "view-media-equalizer-symbolic")

    def preset_list(self, which):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Gtk.Label(label="INCLUIDOS", xalign=0, css_classes=["tb-preset-head"]))
        onboard = Gtk.ListBox(css_classes=["tb-presets"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(onboard)
        box.append(Gtk.Label(label="PERSONALIZADOS (EN EL HEADSET)", xalign=0, css_classes=["tb-preset-head"]))
        custom = Gtk.ListBox(css_classes=["tb-presets"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(custom)
        self.presets[which] = {"onboard": onboard, "custom_box": custom, "custom": [], "active": None, "sig": None}
        for lst in (onboard, custom):
            lst.connect("row-activated", lambda _l, r, wh=which: self.preset_selected(wh, r.index))
        return box

    def preset_row(self, which, index, name, active, deletable):
        r = Gtk.ListBoxRow(activatable=True)
        r.index = index
        b = Gtk.Box(spacing=10)
        b.append(Gtk.Label(label="●" if active else "○", css_classes=["tb-preset-active"] if active else ["dim-label"]))
        b.append(Gtk.Label(label=name.upper(), xalign=0, hexpand=True, ellipsize=3,
                           css_classes=["tb-preset-active"] if active else []))
        if deletable:
            d = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["flat"], tooltip_text="Borrar del headset")
            d.connect("clicked", lambda *_: self.delete_preset(which, name))
            b.append(d)
        r.set_child(b)
        return r

    def fill_presets(self, which, active, custom):
        p = self.presets[which]
        sig = (active, tuple(c["name"] for c in custom))
        p["custom"], p["active"] = custom, active
        if sig == p["sig"]:
            return
        p["sig"] = sig
        builtin = GAME_EQ_PRESETS if which == "game" else MIC_EQ_PRESETS
        for lst in (p["onboard"], p["custom_box"]):
            lst.remove_all()
        if active == 0:
            p["onboard"].append(self.preset_row(which, 0, "Sin guardar", True, False))
        for k, v in builtin.items():
            if k:
                p["onboard"].append(self.preset_row(which, k, v, k == active, False))
        for c in custom:
            p["custom_box"].append(self.preset_row(which, c["index"], c["name"], c["index"] == active, True))
        if not custom:
            r = Gtk.ListBoxRow(activatable=False)
            r.index = None
            r.set_child(Gtk.Label(label="Ninguno",
                                  wrap=True, xalign=0, css_classes=["tb-card-text"]))
            p["custom_box"].append(r)

    def preset_selected(self, which, idx):
        if idx is None or idx == 0 or idx == self.presets[which]["active"]:
            return
        self.fill_presets(which, idx, self.presets[which]["custom"])  # optimistic; bands arrive with the push
        self.worker.do(lambda h: h.use_preset(which, idx))



    def ask_preset_name(self, which):
        entry = Gtk.Entry(placeholder_text="Nombre del preset", max_length=31, activates_default=True)
        d = Adw.AlertDialog(heading="Guardar preset en el headset",
                            body="Se guarda en el headset (máx. 5). El botón «mode» también lo recorre.",
                            extra_child=entry, default_response="save", close_response="cancel")
        d.add_response("cancel", "Cancelar")
        d.add_response("save", "Guardar")
        d.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)

        def done(_d, resp):
            name = entry.get_text().strip()
            if resp != "save" or not name:
                return
            bands = [round(b.get_value() * 10) for b in self.widgets[f"eq_{which}"]]
            self.worker.do(lambda h: h.save_preset(which, name, bands),
                           lambda _: self.toasts.add_toast(Adw.Toast(title=f"Preset «{name}» guardado")))
        d.connect("response", done)
        d.present(self)

    def delete_preset(self, which, name):
        self.worker.do(lambda h: h.delete_preset(which, name),
                       lambda _: self.toasts.add_toast(Adw.Toast(title=f"Preset «{name}» borrado")))

    def focus_stage(self, page):
        if page == "eq":  # the EQ tab decides: earcups for game audio, the boom for the mic
            page = "mic" if self.eq_tabs.get_active_name() == "mic" else "eq"
        self.stage.focus(page)

    def show_eq_notice(self):  # only the mic EQ depends on the mic being open
        self.mic_eq_notice.set_reveal_child(not getattr(self, "mic_enabled", True)
                                            and self.eq_tabs.get_active_name() == "mic")

    def mute_notice(self):
        box = Gtk.Box(spacing=8, css_classes=["tb-mute-notice"])
        box.append(Gtk.Image(icon_name="microphone-sensitivity-muted-symbolic"))
        box.append(Gtk.Label(label="Micrófono silenciado",
                             wrap=True, xalign=0, hexpand=True))
        return Gtk.Revealer(child=box, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=250)

    def set_mic_enabled(self, enabled):
        for n in ("mic_sensitivity", "mic_monitor", "noise_gate", "noise_gate_level"):
            self.widgets[n].set_sensitive(enabled)
        for w in self.mic_parts:
            w.set_sensitive(enabled)
        self.mic_notice.set_reveal_child(not enabled)
        self.mic_enabled = enabled
        self.show_eq_notice()

    # --- device page ---
    def info_list(self):
        self.info_rows = {k: Adw.ActionRow(title=t, subtitle="…", css_classes=["property"])
                          for k, t in (("battery", "BATERÍA"), ("link", "HEADSET"), ("source", "FUENTE ACTIVA"),
                                       ("transmitter_fw", "FIRMWARE TRANSMISOR"), ("headset_fw", "FIRMWARE HEADSET"),
                                       ("daemon", "SERVICIO TB600D"))}
        b = Gtk.ListBox(css_classes=["tb-list"], selection_mode=Gtk.SelectionMode.NONE)
        for r in self.info_rows.values():
            b.append(r)
        return b

    def maintenance_list(self):
        b = Gtk.ListBox(css_classes=["tb-list"], selection_mode=Gtk.SelectionMode.NONE)
        for title, sub, label, css, fn in (
                ("REINICIAR HEADSET", None, "Reiniciar", "tb-outline", lambda: self.confirm_device(
                    "¿Reiniciar el headset?", "El audio se cortará unos segundos.", "Reiniciar", None, lambda h: h.reboot("headset"))),
                ("REINICIAR TRANSMISOR", None, "Reiniciar", "tb-outline", lambda: self.confirm_device(
                    "¿Reiniciar el transmisor?", "El audio se cortará unos segundos.", "Reiniciar", None, lambda h: h.reboot("transmitter"))),
                ("RESTABLECER DE FÁBRICA", None, "Restablecer",
                 "destructive-action", lambda: self.confirm_device(
                    "¿Restablecer el headset de fábrica?", "Se borrarán todos sus ajustes y presets personalizados. No se puede deshacer.",
                    "Restablecer", Adw.ResponseAppearance.DESTRUCTIVE, lambda h: h.factory_reset()))):
            r = Adw.ActionRow(title=title, subtitle=sub)
            btn = Gtk.Button(label=label, valign=Gtk.Align.CENTER, css_classes=[css])
            btn.connect("clicked", lambda _b, fn=fn: fn())
            r.add_suffix(btn)
            b.append(r)
        return b

    def confirm_device(self, heading, body, action, appearance, job):
        d = Adw.AlertDialog(heading=heading, body=body, default_response="cancel", close_response="cancel")
        d.add_response("cancel", "Cancelar")
        d.add_response("go", action)
        if appearance:
            d.set_response_appearance("go", appearance)
        d.connect("response", lambda _d, r: r == "go" and self.worker.do(job))
        d.present(self)

    # --- profiles page ---
    def add_profiles_page(self):
        lst = Gtk.ListBox(css_classes=["tb-list"], selection_mode=Gtk.SelectionMode.NONE)
        entry = Adw.EntryRow(title="GUARDAR AJUSTES ACTUALES COMO…", show_apply_button=True)
        entry.connect("apply", lambda w: (self.save_profile(w.get_text().strip()), w.set_text("")))
        lst.append(entry)
        self.prof_list = Gtk.ListBox(css_classes=["tb-list"], selection_mode=Gtk.SelectionMode.NONE)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=16, margin_bottom=32)
        box.append(self.card("Nuevo perfil", lst))
        box.append(self.card("Perfiles guardados", self.prof_list))
        actions = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6)
        mk = Gtk.Button(label="Crear copia ahora", css_classes=["tb-outline"])
        mk.connect("clicked", lambda *_: self.create_backup())
        imp = Gtk.Button(label="Restaurar desde archivo…", css_classes=["tb-outline"])
        imp.connect("clicked", lambda *_: self.import_backup())
        actions.append(mk)
        actions.append(imp)
        self.backup_list = Gtk.ListBox(css_classes=["tb-list"], selection_mode=Gtk.SelectionMode.NONE)
        bbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bbox.append(actions)
        bbox.append(self.backup_list)
        box2 = self.card("Copias del headset", bbox)
        self.add_wings("profiles", box, box2)
        self.nav_row("profiles", "Perfiles", "document-save-symbolic")
        self.list_profiles()
        self.list_backups()

    # --- device <-> UI --------------------------------------------------
    def user_set(self, name, value, debounce=False):
        # never echo back what the headset already has (guards against signals fired by refreshes)
        if getattr(self, "_loading", False) or self.known.get(name) == value:
            return
        self.busy_until[name] = time.time() + HOLD_S
        if debounce:
            if name in self.pending:
                GLib.source_remove(self.pending[name])
            self.pending[name] = GLib.timeout_add(300, self._flush, name, value)
        else:
            self.known[name] = value
            self.worker.do(lambda h: h.set(name, value))

    def _flush(self, name, value):
        self.pending.pop(name, None)
        self.known[name] = value
        self.worker.do(lambda h: h.set(name, value))
        return False

    def eq_changed(self, which):
        if getattr(self, "_loading", False):
            return
        key = f"eq_{which}"
        self.busy_until[key] = time.time() + HOLD_S
        bands = [round(b.get_value() * 10) for b in self.widgets[key]]
        if key in self.pending:
            GLib.source_remove(self.pending[key])

        def go():
            self.pending.pop(key, None)
            self.worker.do(lambda h: h.set_eq(which, bands))
            return False
        self.pending[key] = GLib.timeout_add(400, go)

    def set_local(self, k, v):
        self.cfg[k] = v
        save_config(self.cfg)

    def refresh(self):
        # tb600d already follows the headset: reuse its state instead of competing for the device
        try:
            raw = open(tb600d.STATE).read()
            d = json.loads(raw)
            if d.get("connected") and "eq" in d and time.time() - d["time"] < 5:
                sig = raw.replace(f'"time": {d["time"]}', "")  # only redraw when something changed
                if sig == getattr(self, "_last_sig", None):
                    return True
                self._last_sig = sig
                self.apply_state({"snap": d["settings"], "game": d["eq"]["game"], "mic": d["eq"]["mic"],
                                  "info": d, "daemon": True, "custom": d.get("custom")})
                return True
        except (OSError, ValueError, KeyError):
            pass
        if not self.in_flight and time.time() - getattr(self, "_direct_t", 0) > 3:  # no daemon: poll gently
            self.in_flight, self._direct_t = True, time.time()
            self.worker.do(lambda h: {"snap": h.snapshot(), "game": h.eq("game"), "mic": h.eq("mic"),
                                      "info": self.info_cache(h),
                                      "custom": {w: h.custom_presets(w) for w in ("game", "mic")}}, self.apply_state)
        return True

    def info_cache(self, h):
        if time.time() - getattr(self, "_info_t", 0) > 60:
            self._info, self._info_t = h.info(), time.time()
        return self._info

    def apply_state(self, st):
        self.in_flight = False
        self.banner.set_revealed(False)
        self.set_wings_sensitive(True)
        now = time.time()
        self._loading = True
        try:
            for name, v in st["snap"].items():
                if self.busy_until.get(name, 0) <= now:
                    self.known[name] = v
                w = self.widgets.get(name)
                if w is None or v is None or self.busy_until.get(name, 0) > now:
                    continue
                if isinstance(w, Adw.SwitchRow):
                    w.set_active(bool(v))
                elif isinstance(w, Adw.ComboRow):
                    if v in w.keys:
                        w.set_selected(w.keys.index(v))
                elif isinstance(w, Adw.EntryRow):
                    if not w.has_focus() and w.get_text() != v:
                        w.set_text(v)
                elif hasattr(w, "scale"):
                    w.scale.set_value(v)
            for which in ("game", "mic"):
                q = st[which]
                if self.busy_until.get(f"eq_{which}", 0) <= now:
                    for b, v in zip(self.widgets[f"eq_{which}"], q["bands"]):
                        b.set_value(v / 10)
                    self.fill_presets(which, q["preset"], (st.get("custom") or {}).get(which, self.presets[which]["custom"]))
        finally:
            self._loading = False
        i, snap = st["info"], st["snap"]
        bat = i.get("battery")
        charging = i.get("charging")
        link = i.get("headset_link", True)
        level = "missing" if bat is None else ("full" if bat > 80 else "good" if bat > 40 else "low" if bat > 15 else "caution")
        self.bat_icon.set_from_icon_name(f"battery-{level}{'-charging' if charging else ''}-symbolic")
        txt = "?" if bat is None else f"{bat}%"
        self.bat_label.set_label(txt + (" · cargando" if charging else ""))
        self.status_bat.set_visible(link is not False)
        muted = bool(snap.get("mic_mute"))
        self.stage.set_link(link is not False)
        self.stage.set_muted(muted)
        self.set_mic_enabled(not muted)
        if link is False:
            self.dot.set_css_classes(["tb-dot-bad"])
            self.status_label.set_label("Headset apagado")
        else:
            self.dot.set_css_classes(["tb-dot-ok"])
            self.status_label.set_label("Conectado" + (" · micrófono silenciado" if snap.get("mic_mute") else ""))
        self.info_rows["battery"].set_subtitle(txt + (" (cargando)" if charging else ""))
        self.info_rows["link"].set_subtitle("apagado o fuera de alcance" if link is False else "conectado")
        self.info_rows["transmitter_fw"].set_subtitle(i.get("transmitter_fw") or "?")
        self.info_rows["headset_fw"].set_subtitle(i.get("headset_fw") or "?")
        self.info_rows["source"].set_subtitle({1: "PC (2.4 GHz)", 0: "Bluetooth"}.get(snap.get("source_pc"), "?"))
        self.info_rows["daemon"].set_subtitle("activo" if st.get("daemon") else
                                              "parado: sin mezcla, SHH ni chat boost (systemctl --user enable --now tb600d)")

    def show_error(self, msg):
        self.in_flight = False
        self.dot.set_css_classes(["tb-dot-bad"])
        self.status_label.set_label("Sin conexión")
        self.stage.set_link(False)
        self.banner.set_title(f"Headset no disponible: {msg}")
        self.banner.set_revealed(True)

    # --- profiles -------------------------------------------------------
    def save_profile(self, name):
        if not name or "/" in name:
            return

        def job(h):
            return {"settings": {k: v for k, v in h.snapshot().items()
                                 if k != "headset_name" and SETTINGS[k].kind != "ro"},
                    "eq": {w: h.eq(w)["bands"] for w in ("game", "mic")},
                    "local": {k: self.cfg[k] for k in LOCAL_KEYS}}

        def done(p):
            os.makedirs(PROFILES, exist_ok=True)
            json.dump(p, open(os.path.join(PROFILES, name + ".json"), "w"), indent=1)
            self.toasts.add_toast(Adw.Toast(title=f"Perfil «{name}» guardado"))
            self.list_profiles()
        self.worker.do(job, done)

    def apply_profile(self, name):
        p = json.load(open(os.path.join(PROFILES, name + ".json")))
        for k, v in p.get("local", {}).items():
            self.cfg[k] = v
        save_config(self.cfg)

        def job(h):
            for k, v in p["settings"].items():
                if k in SETTINGS and v is not None and SETTINGS[k].kind != "ro":
                    h.set(k, v)
            for w, b in p.get("eq", {}).items():
                h.set_eq(w, b)
        self.worker.do(job, lambda _: (self.toasts.add_toast(Adw.Toast(title=f"Perfil «{name}» aplicado")),
                                       self.refresh()))

    def delete_profile(self, name):
        os.remove(os.path.join(PROFILES, name + ".json"))
        self.list_profiles()

    # --- headset backups (exact copy of every group, see Headset.backup/restore) ---
    def create_backup(self):
        name = time.strftime("%Y-%m-%d %H.%M.%S")

        def done(groups):
            os.makedirs(BACKUPS, exist_ok=True)
            json.dump(groups, open(os.path.join(BACKUPS, name + ".json"), "w"), indent=1, ensure_ascii=False)
            self.toasts.add_toast(Adw.Toast(title="Copia del headset creada"))
            self.list_backups()
        self.toasts.add_toast(Adw.Toast(title="Creando copia… (unos 15 s)", timeout=4))
        self.worker.do(lambda h: h.backup(), done)

    def restore_backup(self, path):
        label = os.path.basename(path).removesuffix(".json")
        d = Adw.AlertDialog(heading="¿Restaurar esta copia?",
                            body=f"Los ajustes y presets del headset se sustituirán por los de «{label}».",
                            default_response="cancel", close_response="cancel")
        d.add_response("cancel", "Cancelar")
        d.add_response("go", "Restaurar")
        d.set_response_appearance("go", Adw.ResponseAppearance.SUGGESTED)

        def go(_d, r):
            if r != "go":
                return
            try:
                groups = json.load(open(path))
            except (OSError, ValueError) as e:
                self.show_error(f"copia no válida: {e}")
                return
            self.toasts.add_toast(Adw.Toast(title="Restaurando… (unos 20 s)", timeout=4))
            self.worker.do(lambda h: h.restore(groups),
                           lambda _: self.toasts.add_toast(Adw.Toast(title=f"Copia «{label}» restaurada")))
        d.connect("response", go)
        d.present(self)

    def import_backup(self):
        dlg = Gtk.FileDialog(title="Restaurar copia del headset")
        f = Gtk.FileFilter(name="Copias (JSON)")
        f.add_pattern("*.json")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg.set_filters(filters)
        if os.path.isdir(BACKUPS):
            dlg.set_initial_folder(Gio.File.new_for_path(BACKUPS))

        def picked(d, res):
            try:
                gfile = d.open_finish(res)
            except GLib.Error:
                return  # cancelled
            self.restore_backup(gfile.get_path())
        dlg.open(self, None, picked)

    def delete_backup(self, path):
        os.remove(path)
        self.list_backups()

    def list_backups(self):
        self.backup_list.remove_all()
        files = sorted((f for f in os.listdir(BACKUPS) if f.endswith(".json")), reverse=True) \
            if os.path.isdir(BACKUPS) else []
        if not files:
            self.backup_list.append(Adw.ActionRow(title="Ninguna todavía", css_classes=["dim-label"]))
        for f in files:
            path = os.path.join(BACKUPS, f)
            r = Adw.ActionRow(title=f.removesuffix(".json").upper())
            for icon, tip, fn in (("edit-undo-symbolic", "Restaurar en el headset", self.restore_backup),
                                  ("user-trash-symbolic", "Borrar copia", self.delete_backup)):
                b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER, css_classes=["flat"])
                b.connect("clicked", lambda _b, p=path, fn=fn: fn(p))
                r.add_suffix(b)
            self.backup_list.append(r)

    def list_profiles(self):
        self.prof_list.remove_all()
        names = sorted(f[:-5] for f in os.listdir(PROFILES) if f.endswith(".json")) if os.path.isdir(PROFILES) else []
        if not names:
            self.prof_list.append(Adw.ActionRow(title="Ninguno todavía", css_classes=["dim-label"]))
        for n in names:
            r = Adw.ActionRow(title=n.upper())
            for icon, tip, fn in (("media-playback-start-symbolic", "Aplicar", self.apply_profile),
                                  ("user-trash-symbolic", "Borrar", self.delete_profile)):
                b = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER, css_classes=["flat"])
                b.connect("clicked", lambda _b, n=n, fn=fn: fn(n))
                r.add_suffix(b)
            self.prof_list.append(r)


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        (self.get_active_window() or Window(self)).present()


if __name__ == "__main__":
    App().run(sys.argv)
