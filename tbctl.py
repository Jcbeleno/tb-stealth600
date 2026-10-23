#!/usr/bin/env python3
"""tbctl: control Turtle Beach Stealth 600 Gen 3 PC from the command line.

  tbctl info                    firmware + battery
  tbctl show                    named settings
  tbctl set <name> <value>      e.g. tbctl set noise_gate on, tbctl set chat_mix 70
  tbctl eq game|mic [b1..b10]   read or write the 10 EQ bands (tenths of dB)
  tbctl dump                    every raw group/key on the headset
  tbctl raw-get <group>  /  tbctl raw-set <key> <value>
  tbctl watch [group ...]       print changes made on the headset (buttons, wheel)
  tbctl backup FILE / tbctl restore FILE   all settings + custom presets
  tbctl reboot headset|transmitter
  tbctl factory-reset --yes     erase all headset settings and custom presets
  tbctl toggle <name>           flip an on/off setting and show a desktop notification (for hotkeys)
  tbctl tones [band ...]        play a test tone per EQ band (1-10) on the default output
  tbctl preset game|mic [list | save NAME | use NAME|INDEX | delete NAME]
                                custom EQ presets stored on the headset (max 5 each)
"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from tb600 import (AUTO_SHUTOFF, SETTINGS, Headset, TBError)


def fmt(name, v):
    s = SETTINGS[name]
    if s.kind == "bool":
        return "on" if v else "off"
    if s.kind == "choice" and v is not None:
        ch = s.choices
        return f"{v} ({ch.get(v) if isinstance(ch, dict) else ch[v] if v < len(ch) else '?'})"
    return v


BAND_HZ = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]


def tones(bands):
    """Sine per band at -20 dBFS; compare loudness between bands to hear the EQ curve."""
    import math, struct, subprocess, tempfile, wave
    rate, secs, amp = 48000, 1.2, 0.1
    for b in bands:
        hz = BAND_HZ[b - 1]
        n = int(rate * secs)
        fade = int(rate * 0.02)  # 20 ms ramps avoid clicks
        frames = b"".join(struct.pack("<h", int(32767 * amp * min(1, i / fade, (n - i) / fade)
                                                * math.sin(2 * math.pi * hz * i / rate))) for i in range(n))
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            with wave.open(f.name, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(frames)
            print(f"banda {b}: {hz} Hz", flush=True)
            subprocess.run(["paplay", f.name])
        time.sleep(0.3)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--dev")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("info", "show", "dump"):
        sub.add_parser(c)
    tn = sub.add_parser("tones"); tn.add_argument("bands", nargs="*", type=int)
    w = sub.add_parser("watch"); w.add_argument("groups", nargs="*", help="only these groups (faster)")
    s = sub.add_parser("set"); s.add_argument("name", choices=SETTINGS); s.add_argument("value")
    g = sub.add_parser("get"); g.add_argument("name", choices=SETTINGS)
    bk = sub.add_parser("backup"); bk.add_argument("file")
    rt = sub.add_parser("restore"); rt.add_argument("file")
    rb = sub.add_parser("reboot"); rb.add_argument("target", choices=("headset", "transmitter"))
    fr = sub.add_parser("factory-reset"); fr.add_argument("--yes", action="store_true")
    tg = sub.add_parser("toggle"); tg.add_argument("name", choices=[n for n, s in SETTINGS.items() if s.kind == "bool"])
    e = sub.add_parser("eq"); e.add_argument("which", choices=("game", "mic")); e.add_argument("bands", nargs="*", type=int)
    pr = sub.add_parser("preset"); pr.add_argument("which", choices=("game", "mic"))
    pr.add_argument("action", nargs="?", default="list", choices=("list", "save", "use", "delete"))
    pr.add_argument("name", nargs="?")
    rg = sub.add_parser("raw-get"); rg.add_argument("group")
    rs = sub.add_parser("raw-set"); rs.add_argument("key"); rs.add_argument("value")
    a = ap.parse_args()

    if a.cmd == "tones":
        return tones(a.bands or range(1, 11))
    try:
        h = Headset(a.dev, a.verbose)
        out = lambda o: a.json and (print(json.dumps(o)) or True)  # True = printed, skip text
        if a.cmd == "info":
            i = h.info()
            out(i) or print(f"transmitter {i['transmitter_fw']}  headset {i['headset_fw']}  battery {i['battery']}%")
        elif a.cmd == "show":
            snap = h.snapshot()
            out(snap) or [print(f"{n:<17} {fmt(n, v)!s:<24} {SETTINGS[n].label}") for n, v in snap.items()]
        elif a.cmd == "get":
            v = h.get(a.name)
            out(v) or print(fmt(a.name, v))
        elif a.cmd == "backup":
            groups = h.backup()
            json.dump(groups, open(a.file, "w"), indent=1, ensure_ascii=False)
            print(f"{len(groups)} grupos guardados en {a.file}")
        elif a.cmd == "restore":
            h.restore(json.load(open(a.file)))
            print("restaurado")
        elif a.cmd == "reboot":
            h.reboot(a.target)
            print(f"{a.target}: reiniciando")
        elif a.cmd == "factory-reset":
            if not a.yes:
                sys.exit("Borra todos los ajustes y presets personalizados del headset. Repite con --yes.")
            h.factory_reset()
            print("headset: restableciendo ajustes de fábrica")
        elif a.cmd == "toggle":
            import subprocess
            v = not h.get(a.name)
            h.set(a.name, v)
            label = SETTINGS[a.name].label
            subprocess.run(["notify-send", "-a", "TB Stealth 600", "-t", "1500", "-h", f"string:x-canonical-private-synchronous:tb600-{a.name}",
                            f"{label}: {'activado' if v else 'desactivado'}"])
            print(a.name, "=", fmt(a.name, v))
        elif a.cmd == "set":
            h.set(a.name, a.value)
            print(a.name, "=", fmt(a.name, h.get(a.name)))
        elif a.cmd == "eq":
            if a.bands:
                h.set_eq(a.which, a.bands)
            q = h.eq(a.which)
            out(q) or print(f"preset {q['preset']} '{q['name']}'  bands (dB): "
                            + " ".join(f"{b / 10:+.1f}" for b in q["bands"]))
        elif a.cmd == "preset":
            from tb600 import GAME_EQ_PRESETS, MIC_EQ_PRESETS
            builtin = GAME_EQ_PRESETS if a.which == "game" else MIC_EQ_PRESETS
            custom = h.custom_presets(a.which)
            if a.action != "list" and not a.name:
                sys.exit("error: falta el nombre")
            if a.action == "save":
                h.save_preset(a.which, a.name, h.eq(a.which)["bands"])
            elif a.action == "delete":
                h.delete_preset(a.which, a.name)
            elif a.action == "use":
                by_name = {c["name"]: c["index"] for c in custom} | {v: k for k, v in builtin.items() if k}
                idx = by_name.get(a.name) or (int(a.name) if a.name.isdigit() else None)
                if idx is None:
                    sys.exit(f"error: no existe el preset '{a.name}'")
                h.use_preset(a.which, idx)
            q = h.eq(a.which)
            for k, v in builtin.items():
                if k:
                    print(f"{'*' if q['preset'] == k else ' '} {k:>2} {v}")
            for c in h.custom_presets(a.which):
                print(f"{'*' if q['preset'] == c['index'] else ' '} {c['index']:>2} {c['name']}  "
                      + " ".join(f"{b / 10:+.1f}" for b in c["bands"]))
        elif a.cmd == "dump":
            allg = {g: h.read(g) for g in h.groups()}
            out(allg) or [print(f"[{g}] " + " ".join(f"{k}={v if isinstance(v, str) else json.dumps(v)}"
                                                    for k, v in kv.items())) for g, kv in allg.items()]
        elif a.cmd == "raw-get":
            print(json.dumps(h.read(a.group)))
        elif a.cmd == "raw-set":
            h.write_raw(a.key, a.value)
            print("ok")
        elif a.cmd == "watch":
            # shortcut: polls every group instead of CoAP Observe; fine for a CLI, ~1 s latency
            # BT holds live link stats (RSSI-like), changes every few seconds
            groups = a.groups or [g for g in h.groups() if not g.startswith(("CG", "CM", "Dbg", "Inf", "BT"))]
            prev = {g: h.read(g) for g in groups}
            print("watching", ", ".join(groups), "(Ctrl+C to stop)", flush=True)
            while True:
                time.sleep(0.5)
                for g in groups:
                    try:
                        cur = h.read(g)
                    except TBError:
                        continue
                    for k, v in cur.items():
                        if prev[g].get(k) != v:
                            print(f"{time.strftime('%H:%M:%S')} {g}.{k}: {prev[g].get(k)} -> {v}", flush=True)
                    prev[g] = cur
    except TBError as ex:
        sys.exit(f"error: {ex}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
