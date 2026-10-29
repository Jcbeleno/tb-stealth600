#!/usr/bin/env python3
"""tb600d: background service for the Stealth 600 Gen 3 PC.

- runs the host DSP (TB Game / TB Chat sinks, see tb600_dsp.py)
- follows headset state through CoAP Observe pushes, like Swarm (wheel, buttons, app changes
  arrive within ~0.1 s) and re-applies the DSP
- writes ~/.cache/tb600/state.json for the GUI and the Noctalia battery widget
Local-only options (no headset key) live in ~/.config/tb600/config.json.
"""
import json, os, signal, subprocess, sys, time, zlib
os.environ.setdefault("TB600_TRACE_LINK", "1")  # log raw 0x2CB1 link indications to the journal
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import tb600_dsp as dsp
from tb600 import UPDATES, Headset, TBError, derive

CONFIG = os.path.expanduser("~/.config/tb600/config.json")
STATE = os.path.join(dsp.CACHE, "state.json")
DEFAULTS = {"surround": False, "surround_preset": "game", "route_default": True, "battery_alerts": True}
BATTERY_ALERTS = (15, 5)  # % thresholds, each notified once until the battery is back above the first
LISTEN = 0.15  # s per push-listening slice (the hidraw lock is released between slices)
INFO_EVERY = 600  # s between firmware RACE queries (battery/charging come as GSI pushes)
RESUBSCRIBE_AFTER_LINK = 1.5  # s: subscriptions live in the headset and die when it powers off (swarm2linux)
OBSERVE_EVERY = 1800  # s between full re-reads + Observe re-registration (safety net; link-up re-reads anyway)
YIELD = 0.03  # s without the hidraw lock between listen slices, so the GUI/CLI get their turn quickly
SKIP = ("Dbg", "Inf", "BT")  # BT holds live link stats that change every few seconds


def load_config():
    try:
        return {**DEFAULTS, **json.load(open(CONFIG))}
    except (OSError, ValueError):
        return dict(DEFAULTS)


def pactl(*args):
    # a wedged PipeWire/pipewire-pulse hangs pactl forever: never let it freeze the headset loop
    return subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=dsp.PW_TIMEOUT).stdout


def sinks():
    out = pactl("list", "short", "sinks")
    return [line.split("\t")[1] for line in out.splitlines() if "\t" in line]


def headset_sink(names):
    return next((n for n in names if "Turtle_Beach" in n and "Stealth_600" in n), None)


def default_sink():
    return pactl("get-default-sink").strip()


def fade(sink, up):
    """Short volume ramp + mute on the hardware sink. While the DSP is rebuilt, TB Game is gone and apps fall back to
    the hardware sink, which sits at 100%: unmuted, that is a loud burst of unprocessed audio."""
    if up:
        pactl("set-sink-volume", sink, "0%")
        pactl("set-sink-mute", sink, "0")
    for v in ((25, 50, 75, 100) if up else (75, 50, 25, 0)):
        pactl("set-sink-volume", sink, f"{v}%")
        time.sleep(0.03)
    if not up:
        pactl("set-sink-mute", sink, "1")


def sink_volume(name):
    out = pactl("get-sink-volume", name)
    pct = [w for w in out.split() if w.endswith("%")]
    return pct[0] if pct else "100%"


VOLUMES = os.path.join(dsp.CACHE, "volumes.json")


def load_volumes():
    try:
        return json.load(open(VOLUMES))
    except (OSError, ValueError):
        return {}


def save_volumes(v):
    if v != load_volumes():
        json.dump(v, open(VOLUMES, "w"))


RAM = os.path.join(dsp.CACHE, "headset_ram.json")
# kept only in headset RAM: after power-off the name is back to "My Headset" and the game/chat mix to 0 (chat silent)
RAM_KEYS = {"220": "GSI", "510": "Swm"}
RAM_DEFAULTS = {"510": "50"}


def load_ram():
    try:
        return {**RAM_DEFAULTS, **json.load(open(RAM))}
    except (OSError, ValueError):
        return dict(RAM_DEFAULTS)


def save_ram(groups):
    ram = {k: groups[g][k] for k, g in RAM_KEYS.items() if k in groups.get(g, {})}
    if ram != load_ram():
        json.dump(ram, open(RAM, "w"))


def write_state(st):
    tmp = STATE + ".tmp"
    json.dump(st, open(tmp, "w"))
    os.replace(tmp, STATE)


def spooled():
    """Pushes that other processes (GUI/CLI) read off the shared buffer."""
    tmp = UPDATES + ".reading"
    try:
        os.replace(UPDATES, tmp)
    except OSError:
        return []
    out = []
    for line in open(tmp):
        try:
            j = json.loads(line)
            out.append((j["UP"], j["KVP"]))
        except (ValueError, KeyError):
            pass
    os.remove(tmp)
    return out


def battery_alert(pct, cfg, notified, charging=False):
    """Desktop notification when crossing a threshold (Swarm: CE_UPDATE_DEVICE_INFO LOW BATTERY)."""
    if pct is None:
        return
    if pct > BATTERY_ALERTS[0] or charging:
        notified.clear()
        return
    for th in BATTERY_ALERTS:
        if pct <= th and th not in notified:
            notified.add(th)
            if cfg["battery_alerts"]:
                subprocess.run(["notify-send", "-a", "TB Stealth 600", "-i", "battery-caution",
                                "-u", "critical" if th <= 5 else "normal",
                                f"Batería del headset al {pct}%", "Conecta el Stealth 600 para cargarlo."])


def smooth_battery(shown, raw, charging):
    """The gauge jitters ±1 %: only move in the direction the battery can go (down on battery, up while
    charging); a jump of 3+ against it (charged elsewhere, battery swapped) is taken as real."""
    if raw is None or shown is None:
        return raw if raw is not None else shown
    if charging:
        return max(shown, raw) if raw > shown - 3 else raw
    return min(shown, raw) if raw < shown + 3 else raw


def token(group):
    return zlib.crc32(group.encode()).to_bytes(4, "big")  # stable per group: re-observing replaces


def main():
    os.makedirs(dsp.CACHE, exist_ok=True)
    proc, base, h, prev_default, hw_vol, sink = None, None, None, None, None, None
    applied, info, info_t, groups, obs_t, last_write, sink_t = None, {}, 0, {}, 0, 0, 0
    link = True  # assumed until a 0x2CB1 indication says otherwise
    shown_bat = None
    dsp_preset = None
    init_vol, apply_t, muted = False, 0, 0
    eq_check = {}
    running = True
    notified = set()

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while running:
        cfg = load_config()
        st = {"connected": False, "time": time.time()}
        changed = due = False
        want = None
        try:
            if time.time() - sink_t > 2:  # pactl is a subprocess: don't run it every listen slice
                names, sink_t = sinks(), time.time()
                sink = headset_sink(names)
                if "tb600_game" in names and "tb600_chat" in names:
                    save_volumes({s: sink_volume(s) for s in ("tb600_game", "tb600_chat")})
                    if sink and sink_volume(sink) != "100%":
                        # the user's volume lives on the virtual sinks; anything below 100% here (an audio-stack
                        # restart, WirePlumber restoring an old value) would silently attenuate everything twice
                        pactl("set-sink-volume", sink, "100%")
                if base and (not sink or "tb600_game" not in names or "tb600_chat" not in names):
                    # the transmitter re-enumerated (mode switch, replug): the old graph lost its target
                    print("DSP sinks missing: restarting the DSP", flush=True)
                    for p in [proc, *base]:
                        if p and p.poll() is None:
                            p.terminate()
                            p.wait(timeout=5)
                    proc, base = None, None
                if cfg["route_default"] and sink and default_sink() not in ("tb600_game",):
                    prev_default = prev_default or default_sink()
                    pactl("set-default-sink", "tb600_game")
            if not sink:
                raise TBError("headset sink not present")
            want_preset = cfg["surround_preset"] if cfg["surround"] else None  # 3D off: graph without convolvers
            if base is None or any(p.poll() is not None for p in base):
                for p in [proc, *(base or [])]:
                    if p and p.poll() is None:
                        p.terminate()
                proc, base = None, dsp.start_sinks(sink)
                init_vol = True
            if proc and proc.poll() is None and want_preset != dsp_preset:
                # 3D speaker angles live in the convolvers: rebuild the game DSP (~1 s gap). TB Game stays, so
                # players keep their stream; the ramp avoids a click where the old graph is cut off
                fade(sink, up=False)
                muted = time.time() + 4  # unmuted once the new graph has its controls (or after 4 s regardless)
                proc.terminate()
                proc.wait(timeout=5)
                proc = None
            if proc is None or proc.poll() is not None:
                dsp_preset = want_preset
                proc = dsp.start_fx(sink, dsp_preset)
                applied = None
                time.sleep(1.5)
            if init_vol and time.time() >= sink_t:  # retried until pactl answers: never leave TB Game at a default volume
                # Like Windows' Game/Chat endpoints: the user's volume lives on TB Game (the default sink),
                # the hardware sink sits at 100% so TB Game can reach full loudness. Restored on exit.
                hw_vol = hw_vol or sink_volume(sink)
                saved = load_volumes()  # user volumes survive DSP/daemon restarts; first run inherits the headset's
                for s in ("tb600_game", "tb600_chat"):
                    pactl("set-sink-volume", s, saved.get(s, hw_vol))
                pactl("set-sink-volume", sink, "100%")
                init_vol = False
            if h is None:
                h = Headset()
                obs_t = 0
            if time.time() - obs_t > OBSERVE_EVERY:  # also right after connect and after every headset link-up
                # Swarm writes Swm.500=1 on connect / 0 on exit: "host app present". Without it the wheel's
                # game/chat mix is never reported in Swm.510. It lives in headset RAM (see RAM_KEYS): lost when
                # the headset powers off
                h.write_raw("500", "1")
                groups = {g: h.read(g, observe=0, token=token(g)) for g in h.groups() if g not in SKIP}
                for k, v in load_ram().items():
                    if k in groups.get(RAM_KEYS[k], {}) and groups[RAM_KEYS[k]][k] != v:
                        h.write_raw(k, v)
                        groups[RAM_KEYS[k]][k] = v
                obs_t, changed = time.time(), True
            if time.time() - info_t > INFO_EVERY:
                info, info_t, changed = h.info(), time.time(), True
            pushes = h.updates(LISTEN) + spooled()
            time.sleep(YIELD)  # flock isn't fair: give waiting writers (GUI, tbctl) the device now
            for g, kvp in pushes:
                if g in ("AQG", "AQM") and ({"1210", "1310"} & kvp.keys()):
                    eq_check[g] = time.time() + 0.4  # preset switched: re-read the whole EQ shortly after
                if g == "__LINK__":
                    link = kvp["up"]
                    print(f"headset link {'up: resubscribing' if link else 'down'}", flush=True)
                    if link:  # headset (re)connected: subscribe again shortly, refresh firmware/battery
                        obs_t, info_t = time.time() - OBSERVE_EVERY + RESUBSCRIBE_AFTER_LINK, 0
                else:
                    groups.setdefault(g, {}).update(kvp)
                changed = True
            for g, t_due in list(eq_check.items()):
                if time.time() >= t_due:
                    del eq_check[g]
                    fresh = h.read(g)
                    if fresh != {k: v for k, v in groups.get(g, {}).items() if k in fresh}:
                        print(f"{g}: missed push corrected", flush=True)
                    groups.setdefault(g, {}).update(fresh)
                    changed = True
            if changed:
                save_ram(groups)
            due = changed or time.time() - last_write > 1
            if due:
                settings, eq, custom = derive(groups)
                charging = bool(settings.get("charging"))
                raw = settings.get("battery")
                raw = raw if raw is not None and 0 <= raw <= 100 else info.get("battery")
                bat = shown_bat = smooth_battery(shown_bat, raw, charging)
                battery_alert(bat, cfg, notified, charging)
                st.update(connected=True, **{**info, "battery": bat}, charging=charging, headset_link=link,
                          settings=settings, eq=eq, custom=custom, surround=cfg["surround"])
                want = {k: settings[k] for k in ("chat_mix", "shh", "shh_level", "shh_preset", "chat_boost")}
                want.update(surround=cfg["surround"], surround_preset=cfg["surround_preset"])
        except TBError as e:
            st["error"] = str(e)
            if h:
                h.close()
            h, info_t, sink_t, due = None, 0, 0, True
            time.sleep(1)
        except subprocess.TimeoutExpired as e:
            print(f"{e.cmd[0]} timed out: PipeWire not answering, audio checks paused 10 s", flush=True)
            sink_t = time.time() + 10
        if due:
            write_state(st)
            last_write = time.time()
        # after the state write, so a slow PipeWire never delays what the GUI shows
        if want and want != applied and time.time() >= apply_t:
            applied, apply_t = (want, 0) if dsp.apply(want) else (applied, time.time() + 3)
        if muted and sink and (applied == want or time.time() > muted):
            try:
                fade(sink, up=True)
                muted = 0
            except subprocess.TimeoutExpired:
                pass

    if h:
        try:
            for g in groups:
                h.read(g, observe=1, token=token(g))  # cancel our subscriptions
            h.write_raw("500", "0")
        except TBError:
            pass
    try:
        if prev_default:
            pactl("set-default-sink", prev_default)
        if sink:  # hand the user's game volume back to the hardware sink (it sat at 100% while we ran)
            pactl("set-sink-volume", sink, load_volumes().get("tb600_game", hw_vol or "50%"))
            if muted:  # stopped mid 3D switch
                pactl("set-sink-mute", sink, "0")
    except subprocess.TimeoutExpired:
        pass
    for p in [proc, *(base or [])]:
        if p and p.poll() is None:
            p.terminate()


if __name__ == "__main__":
    main()
