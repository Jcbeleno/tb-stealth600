#!/usr/bin/env python3
"""Turtle Beach Stealth 600 Gen 3 PC (10f5:224c) protocol library.

Protocol reverse-engineered from Swarm II (Turtlebeach.Mosit.dll, STEALTH_600.dll):
  hidraw out report 0x06 / in report 0x07, 61 data bytes:
      [len_lo][len_hi or 0x80=OTA/relay][Airoha RACE packet ...]
  RACE packet: 05 <type 5A cmd|5B resp|5D notify> <len u16 LE = 2 + payload> <id u16 LE> <payload>
  Settings: CoAP (RFC 7252) carried in RACE id 0x9902.
      GET index           -> comma list of resource keys
      GET <key>           -> value (Swarm uses Observe)
      PUT set_kvp {"0x<key>":"<value>"}
"""
import fcntl, glob, json, os, random, struct, sys, time

VID, PID = 0x10F5, 0x224C
RACE_COAP = 0x9902
REPORT_LEN = 61
UPDATES = os.path.expanduser("~/.cache/tb600/updates.jsonl")


def HIDIOCGINPUT(n):  # _IOC(_IOC_READ|_IOC_WRITE, 'H', 0x0A, n)
    return (3 << 30) | (n << 16) | (ord("H") << 8) | 0x0A


class TBError(Exception):
    pass


def find_hidraw():
    for p in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        ue = open(p + "/device/uevent").read()
        if f"{VID:08X}:{PID:08X}" not in ue:
            continue
        desc = open(p + "/device/report_descriptor", "rb").read()
        if b"\x06\x13\xff" in desc:  # vendor usage page 0xFF13 = RACE channel
            return "/dev/" + os.path.basename(p)
    raise TBError("Stealth 600 Gen 3 (10f5:224c) not connected")


def _locked(fn):
    # one transaction at a time across processes (CLI, GUI, daemon share the hidraw node)
    def w(self, *a, **k):
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        try:
            return fn(self, *a, **k)
        finally:
            # a push read in the same report as our response stays in self.buf: hand it to tb600d now,
            # not at this process's next transaction (or never, if it exits)
            for p in self.packets(0):
                self._spill(p)
            fcntl.flock(self.fd, fcntl.LOCK_UN)
    return w


class Dev:
    def __init__(self, path, verbose=False):
        try:
            self.fd = os.open(path, os.O_RDWR)
        except PermissionError:
            raise TBError(f"No permission on {path}: install 70-tb-stealth600.rules (see README)")
        self.v = verbose
        self.buf = bytearray()

    def _write(self, data, flag=0):
        for i in range(0, len(data), 59):
            chunk = data[i:i + 59]
            rep = bytes([0x06, len(chunk), flag]) + chunk
            rep += bytes(1 + REPORT_LEN - len(rep))
            if self.v:
                print(">>", rep[:3 + len(chunk)].hex(" "), file=sys.stderr)
            os.write(self.fd, rep)

    def _read(self, timeout):
        # Swarm polls with HidD_GetInputReport (control GET_REPORT), not interrupt IN -> HIDIOCGINPUT
        end = time.time() + timeout
        while True:
            buf = bytearray(1 + REPORT_LEN)
            buf[0] = 0x07
            try:
                fcntl.ioctl(self.fd, HIDIOCGINPUT(len(buf)), buf)
            except OSError:
                buf[1] = 0
            if buf[1]:
                break
            if time.time() >= end:
                return None
            time.sleep(0.02)
        rep = bytes(buf)
        n = rep[1]
        data = rep[3:3 + n]
        if self.v:
            print("<<", rep[:3 + n].hex(" "), file=sys.stderr)
        return data

    def packets(self, timeout):
        """Yield RACE packets; one input report may hold several, one packet may span reports."""
        end = time.time() + timeout
        while True:
            while len(self.buf) >= 4:
                if self.buf[0] != 5:  # resync on garbage
                    i = self.buf.find(5, 1)
                    del self.buf[:i if i > 0 else len(self.buf)]
                    continue
                n = 4 + struct.unpack_from("<H", self.buf, 2)[0]
                if len(self.buf) < n:
                    break
                pkt = bytes(self.buf[:n])
                del self.buf[:n]
                yield pkt
            left = end - time.time()
            if left <= 0:
                # Never give the device up with half a packet buffered: the next process would read the
                # rest off the shared input stream and the whole push (e.g. a preset's bands) would be lost.
                if not self.buf or left < -0.5:
                    return
                left = 0.05
            d = self._read(left)
            if d is None:
                if time.time() >= end and self.buf:
                    continue
                return
            self.buf += d

    def drain(self):
        while (d := self._read(0.05)) is not None:
            self.buf += d
        for p in self.packets(0):
            self._spill(p)
        self.buf = bytearray()

    def _notification(self, p):
        """CoAP Observe push ({"UP":group,"KVP":{...}}) -> (group, kvp), else None. ACKs CON pushes."""
        if not (len(p) > 6 and p[4:6] == b"\x02\x99"):
            return None
        r = self._parse_coap(p[6:])
        if not r or not r["payload"].startswith(b'{"UP"'):
            return None
        if r["type"] == 0:
            self._write(bytes([0x05, 0x5A]) + struct.pack("<HH", 6, RACE_COAP) + bytes([0x60, 0]) + struct.pack(">H", r["mid"]))
        try:
            j = json.loads(r["payload"])
            return j["UP"], j.get("KVP", {})
        except (ValueError, KeyError):
            return None

    @staticmethod
    def _link(p):
        """RACE 0x2CB1 indication: radio link to the headset, payload byte 2 = 1 up / 0 down."""
        if len(p) >= 9 and p[1] == 0x5D and struct.unpack_from("<H", p, 4)[0] == 0x2CB1:
            if os.environ.get("TB600_TRACE_LINK"):
                print("0x2CB1", p[6:].hex(" "), file=sys.stderr, flush=True)
            return p[8] != 0
        return None

    def _spill(self, p):
        # The input buffer is shared: a push read by the CLI/GUI is handed to tb600d through this file.
        n = self._notification(p)
        link = self._link(p)
        if link is not None:
            n = ("__LINK__", {"up": link})
        if n:
            try:
                with open(UPDATES, "a") as f:
                    f.write(json.dumps({"t": time.time(), "UP": n[0], "KVP": n[1]}) + "\n")
            except OSError:
                pass

    @_locked
    def updates(self, timeout):
        """Pushes received within timeout (caller owns them, nothing is spilled)."""
        out = []
        for p in self.packets(timeout):
            n = self._notification(p)
            link = self._link(p)
            if link is not None:
                n = ("__LINK__", {"up": link})
            if n:
                out.append(n)
        return out

    @_locked
    def race(self, rid, payload=b"", flag=0, timeout=2.0, want=(0x5B, 0x5D)):
        """Send RACE command, return first matching packet (type in want, same id)."""
        self.drain()
        self._write(bytes([0x05, 0x5A]) + struct.pack("<HH", len(payload) + 2, rid) + payload, flag)
        for p in self.packets(timeout):
            if len(p) >= 6 and p[1] in want and struct.unpack_from("<H", p, 4)[0] == rid:
                return p
            self._spill(p)
        return None

    # --- CoAP over RACE 0x9902 ---
    @_locked
    def coap(self, code, path, payload=b"", observe=None, timeout=5.0, token=None):
        """observe: None = plain request, 0 = register, 1 = deregister (same token replaces/cancels)."""
        mid = random.randrange(0x10000)
        tok = token or os.urandom(4)
        msg = bytes([0x40 | len(tok), code]) + struct.pack(">H", mid) + tok
        opts = ([] if observe is None else [(6, bytes([observe]) if observe else b"")]) + \
            [(11, seg.encode()) for seg in path.split("/")]
        if payload:
            opts.append((12, bytes([50])))  # Content-Format: application/json
        last = 0
        for num, val in opts:
            delta, ln = num - last, len(val)
            assert delta < 13 and ln < 13, "shortcut: no extended option encoding; paths are short"
            msg += bytes([(delta << 4) | ln]) + val
            last = num
        if payload:
            msg += b"\xff" + payload
        self.drain()
        self._write(bytes([0x05, 0x5A]) + struct.pack("<HH", len(msg) + 2, RACE_COAP) + msg)
        for p in self.packets(timeout):
            if not (len(p) > 6 and p[1] in (0x5B, 0x5D) and p[4:6] == b"\x02\x99"):
                self._spill(p)  # a 0x2CB1 link indication lost here left tb600d waiting for a link-up that came
                continue
            resp = self._parse_coap(p[6:])
            if not (resp and resp["token"] == tok):
                self._spill(p)
            elif resp:
                if resp["type"] == 0 and resp["code"]:  # separate CON response -> ACK it
                    self._write(bytes([0x05, 0x5A]) + struct.pack("<HH", 6, RACE_COAP)
                                + bytes([0x60, 0]) + struct.pack(">H", resp["mid"]))
                if resp["code"]:
                    return resp
        return None

    @staticmethod
    def _parse_coap(b):
        if len(b) < 4 or b[0] >> 6 != 1:
            return None
        tkl = b[0] & 0xF
        r = {"type": (b[0] >> 4) & 3, "code": b[1], "mid": struct.unpack_from(">H", b, 2)[0],
             "token": b[4:4 + tkl], "payload": b""}
        i = 4 + tkl
        while i < len(b):
            if b[i] == 0xFF:
                r["payload"] = b[i + 1:]
                break
            delta, ln = b[i] >> 4, b[i] & 0xF
            i += 1
            for v in (delta, ln):
                if v == 13: i += 1
                elif v == 14: i += 2
            if ln == 13: ln = b[i - 1] + 13
            i += ln
        return r


def code_str(c):
    return f"{c >> 5}.{c & 31:02d}"




# --- Settings model -------------------------------------------------------
# Keys come from STEALTH_600.dll: group/key map, owner-interface setters and
# the named UI properties ("Mute", "Sensitivity_Level", ...). Some writes touch
# two keys because Swarm does the same (SHH -> 800 + 530, chat boost -> 540 + 270).
AUTO_SHUTOFF = ["Desactivado", "5 min", "10 min", "20 min", "30 min"]
BUTTON_FUNCS = ["Cambiar preset de juego", "Noise gate on/off", "Chat Boost on/off"]
SHH_PRESETS = ["Por defecto", "Pasos", "Disparos"]  # SHH.810 accepts 0-2 (probed); Swarm: Default/Footsteps/Gunshots
# built-in presets (enumerated on device: index 5 -> CoAP 4.00); writing the index loads bands + name
GAME_EQ_PRESETS = {0: "Personalizado", 1: "Signature Sound", 2: "Bass Boost", 3: "Bass & Treble Boost", 4: "Vocal Boost"}
MIC_EQ_PRESETS = {0: "Personalizado", 1: "Signature Sound", 2: "Full", 3: "Clarity", 4: "Smooth"}
WHEEL_FUNCS = {1: "Volumen de monitorización", 2: "Mezcla juego/chat", 3: "Refuerzo de graves",
               4: "Refuerzo de agudos", 5: "Volumen del noise gate"}


class S:
    def __init__(self, group, keys, kind, label, lo=0, hi=100, choices=None):
        self.group, self.keys, self.kind, self.label = group, keys, kind, label
        self.lo, self.hi, self.choices = lo, hi, choices

    @property
    def key(self):
        return self.keys[0]

    def encode(self, v):
        if self.kind == "ro":
            raise TBError(f"{self.label} es de solo lectura")
        if self.kind == "bool":
            v = v if isinstance(v, bool) else str(v).lower() in ("1", "on", "true", "yes")
            return "1" if v else "0"
        if self.kind == "choice":
            keys = list(self.choices) if isinstance(self.choices, dict) else range(len(self.choices))
            try:
                v = int(v)
            except ValueError:
                raise TBError(f"{self.label}: '{v}' no es un número")
            if v not in keys:
                raise TBError(f"{self.label}: {v} not in {list(keys)}")
            return str(v)
        if self.kind == "int":
            try:
                v = int(v)
            except ValueError:
                raise TBError(f"{self.label}: '{v}' no es un número")
            if not self.lo <= v <= self.hi:
                raise TBError(f"{self.label}: {v} outside {self.lo}..{self.hi}")
            return str(v)
        return str(v)

    def decode(self, s):
        if self.kind == "bool":
            return s == "1"
        if self.kind in ("int", "choice", "ro"):
            try:
                return int(s)
            except ValueError:
                return None
        return s


SETTINGS = {
    "mic_mute":         S("Mic", ["600"], "bool", "Silenciar micrófono"),
    "mic_sensitivity":  S("Mic", ["610"], "int", "Sensibilidad del micrófono"),
    "mic_monitor":      S("Mic", ["630"], "int", "Monitorización del micrófono (0 = apagada)"),
    "noise_gate":       S("NG", ["700"], "bool", "Noise gate"),
    "noise_gate_level": S("NG", ["710"], "int", "Nivel del noise gate"),
    "chat_mix":         S("Swm", ["510"], "int", "Mezcla juego/chat"),
    "chat_boost":       S("Swm", ["540", "270"], "bool", "Chat Boost"),
    "shh":              S("SHH", ["800", "530"], "bool", "Superhuman Hearing"),
    "shh_preset":       S("SHH", ["810"], "choice", "Preset de Superhuman Hearing", choices=SHH_PRESETS),
    "shh_level":        S("SHH", ["820"], "int", "Nivel de Superhuman Hearing"),
    "prompts":          S("Prm", ["c00"], "int", "Volumen de avisos de voz y tonos"),
    "auto_shutoff":     S("GSI", ["280"], "choice", "Apagado automático", choices=AUTO_SHUTOFF),
    "led_brightness":   S("TX", ["430"], "int", "Brillo del LED del transmisor"),
    # layers: a20/b20 = PC (USB, id 0), a10/b10 = Xbox (id 1); from Swarm's key-assign code + embedded [USB]/[XBOX] profile
    "button":           S("Btn", ["b20"], "choice", "Función del botón (PC)", choices=BUTTON_FUNCS),
    "button_xbox":      S("Btn", ["b10"], "choice", "Función del botón (Xbox)", choices=BUTTON_FUNCS),
    "wheel":            S("Enc", ["a20"], "choice", "Función de la rueda (PC)", choices=WHEEL_FUNCS),
    "wheel_xbox":       S("Enc", ["a10"], "choice", "Función de la rueda (Xbox)", choices=WHEEL_FUNCS),
    "game_eq_preset":   S("AQG", ["1210"], "choice", "Preset EQ de juego", choices=GAME_EQ_PRESETS),
    "mic_eq_preset":    S("AQM", ["1310"], "choice", "Preset EQ de micrófono", choices=MIC_EQ_PRESETS),
    "headset_name":     S("GSI", ["220"], "str", "Nombre del headset"),
    "source_pc":        S("GSI", ["290"], "ro", "Fuente activa: 1 = PC (2.4 GHz), 0 = Bluetooth"),
    "battery":          S("GSI", ["240"], "ro", "Batería (%; 255 = desconocida)"),
    "charging":         S("GSI", ["250"], "ro", "Cargando"),
}
EQ_BANDS = {"game": ("AQG", [f"12{b:x}0" for b in range(2, 12)], "1210", "12c0"),
            "mic": ("AQM", [f"13{b:x}0" for b in range(2, 12)], "1310", "13c0")}
EQ_LIMIT = 90  # tenths of dB; headset rejects |v| > 90 with CoAP 4.00 (probed on device)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def custom_from(groups, which):
    """Custom presets from cached CGn/CMn groups."""
    prefix, base, _ = Headset.CUSTOM[which]
    out = []
    for n in range(1, Headset.CUSTOM_SLOTS + 1):
        v = groups.get(f"{prefix}{n}", {}).get(f"{base + 0x20 * (n - 1):x}", {})
        if isinstance(v, dict) and v.get("name"):
            out.append({"index": 15 + n, "name": v["name"], "bands": [_int(x) for x in v.get("bands", [])]})
    return out


def derive(groups):
    """Named settings, both EQs and custom presets from a {group: kvp} cache."""
    settings = {n: s.decode(groups.get(s.group, {}).get(s.key, "")) for n, s in SETTINGS.items()}
    eq = {}
    for w, (grp, bands, idx, nkey) in EQ_BANDS.items():
        kv = groups.get(grp, {})
        eq[w] = {"preset": _int(kv.get(idx)), "name": kv.get(nkey, ""), "bands": [_int(kv.get(b)) for b in bands]}
    return settings, eq, {w: custom_from(groups, w) for w in ("game", "mic")}


class Headset:
    """High-level access: groups are cached per read, writes go through set_kvp."""

    def __init__(self, path=None, verbose=False):
        self.dev = Dev(path or find_hidraw(), verbose)

    def close(self):
        os.close(self.dev.fd)

    def groups(self):
        r = self.dev.coap(0x01, "index")
        if not r:
            raise TBError("no response to GET index")
        return json.loads(r["payload"])

    def read(self, group, observe=None, token=None):
        # plain GET; only tb600d registers Observe (with a fixed token per group, so re-registering replaces)
        r = self.dev.coap(0x01, group, observe=observe, token=token)
        if not r or r["code"] >> 5 != 2:
            raise TBError(f"GET {group} failed")
        try:
            return json.loads(r["payload"]).get("KVP", {})
        except (ValueError, AttributeError) as e:
            raise TBError(f"GET {group}: bad payload ({e})")

    def updates(self, timeout):
        return self.dev.updates(timeout)

    def write_raw(self, key, value):
        key = key if key.startswith("0x") else "0x" + key
        try:  # audit trail: which program changed what (settings can also change from the headset itself)
            with open(os.path.expanduser("~/.cache/tb600/writes.log"), "a") as f:
                f.write(f"{time.strftime('%F %T')} {os.path.basename(sys.argv[0])}[{os.getpid()}] {key}={value}\n")
        except OSError:
            pass
        r = self.dev.coap(0x03, "set_kvp", json.dumps({key: value}, separators=(",", ":")).encode())
        if not r or r["code"] >> 5 != 2:
            raise TBError(f"set {key}={value} failed: {r and code_str(r['code'])}")

    def get(self, name):
        s = SETTINGS[name]
        return s.decode(self.read(s.group).get(s.key, ""))

    def set(self, name, value):
        s = SETTINGS[name]
        enc = s.encode(value)
        for k in s.keys:
            self.write_raw(k, enc)

    def snapshot(self):
        """All named settings, one GET per group."""
        cache, out = {}, {}
        for name, s in SETTINGS.items():
            if s.group not in cache:
                cache[s.group] = self.read(s.group)
            out[name] = s.decode(cache[s.group].get(s.key, ""))
        return out

    def eq(self, which):
        grp, bands, idx, name = EQ_BANDS[which]
        kv = self.read(grp)
        return {"preset": int(kv.get(idx, 0)), "name": kv.get(name, ""),
                "bands": [int(kv.get(b, 0)) for b in bands]}

    def set_eq(self, which, bands, name=None, preset=None):
        """bands: 10 ints in tenths of dB. Same key order Swarm writes (index, bands, name)."""
        grp, keys, idx, nkey = EQ_BANDS[which]
        if len(bands) != 10 or any(abs(int(b)) > EQ_LIMIT for b in bands):
            raise TBError(f"need 10 bands within ±{EQ_LIMIT}")
        if preset is not None:
            self.write_raw(idx, str(int(preset)))
        for k, b in zip(keys, bands):
            self.write_raw(k, str(int(b)))
        if name is not None:
            self.write_raw(nkey, name)

    # --- custom EQ presets stored on the headset (verified on device) ---
    # create: write the 10 bands, then a new name in 12c0/13c0 -> firmware stores it in the first free
    #         CGn/CMn slot and selects it; slot n is preset index 15+n (16..20); slots are not compacted.
    # delete: write the preset name to CEC 1610 (game) / 1630 (mic).
    CUSTOM = {"game": ("CG", 0x1700, "1610"), "mic": ("CM", 0x1800, "1630")}
    CUSTOM_SLOTS = 5

    def custom_presets(self, which):
        prefix = self.CUSTOM[which][0]
        return custom_from({f"{prefix}{n}": self.read(f"{prefix}{n}") for n in range(1, self.CUSTOM_SLOTS + 1)}, which)

    def save_preset(self, which, name, bands):
        name = name.strip()
        existing = self.custom_presets(which)
        if not name or len(name) > 31:  # firmware struct keeps the name in a small fixed buffer
            raise TBError("nombre vacío o demasiado largo (máx. 31)")
        if any(p["name"] == name for p in existing):
            self.delete_preset(which, name)  # overwrite = delete + create
        elif len(existing) >= self.CUSTOM_SLOTS:
            raise TBError(f"ya hay {self.CUSTOM_SLOTS} presets personalizados; borra uno")
        self.set_eq(which, bands)
        self.write_raw(EQ_BANDS[which][3], name)

    def use_preset(self, which, index):
        self.write_raw(EQ_BANDS[which][2], str(int(index)))

    def delete_preset(self, which, name):
        self.write_raw(self.CUSTOM[which][2], name)

    def backup(self):
        """Every group as the headset reports it (restore() puts it back)."""
        return {g: self.read(g) for g in self.groups() if g not in ("Dbg", "Inf", "BT")}

    def restore(self, groups):
        """Writable settings, custom presets (re-created by name) and the active EQ of a backup()."""
        settings, eq, custom = derive(groups)
        for name, s in SETTINGS.items():
            v = settings.get(name)
            if s.kind != "ro" and v is not None and name not in ("game_eq_preset", "mic_eq_preset"):
                self.set(name, v)
        for which in ("game", "mic"):
            have = {c["name"] for c in self.custom_presets(which)}
            for c in custom[which]:
                if c["name"] not in have:
                    self.save_preset(which, c["name"], c["bands"])
            q = eq[which]
            if q["preset"] >= 16:  # slot numbers can differ after re-creating: select by name
                by_name = {c["name"]: c["index"] for c in self.custom_presets(which)}
                if q["name"] in by_name:
                    self.use_preset(which, by_name[q["name"]])
            elif q["preset"]:
                self.use_preset(which, q["preset"])
            else:
                self.set_eq(which, q["bands"])

    # Airoha "app key" RACE 0x1101 (Mosit KeyCodeCmds): 25 = System Reboot, 149 = Factory Reset.
    # flag 0x80 relays through the transmitter to the headset; no reply is awaited (the device drops off).
    def reboot(self, target="headset"):
        self.dev._write(bytes([0x05, 0x5A]) + struct.pack("<HHH", 4, 0x1101, 25), 0x80 if target == "headset" else 0)

    def factory_reset(self):
        """Erases every headset setting and custom preset (Swarm: "Reset")."""
        self.dev._write(bytes([0x05, 0x5A]) + struct.pack("<HHH", 4, 0x1101, 149), 0x80)

    def info(self):
        out = {}
        for name, flag in (("transmitter_fw", 0), ("headset_fw", 0x80)):
            v = self.dev.race(0x1C07, b"\x00", flag, want=(0x5D,))
            out[name] = v[9:].decode(errors="replace") if v else None
        b = self.dev.race(0x0CD6, b"\x00", 0x80, want=(0x5D,))
        out["battery"] = b[8] if b and len(b) > 8 and b[6] == 0 else None
        return out
