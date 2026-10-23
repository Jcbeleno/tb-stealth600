"""Host-side DSP that Swarm II delegates to the Waves APO on Windows.

Runs a private `pipewire -c <conf>` process with two filter-chain sinks feeding the headset:
  TB Game : low shelf + peaking (Superhuman Hearing) -> optional crossfeed virtual speakers (Waves 3D)
  TB Chat : high-pass + presence peak (Chat Boost)
Game/chat mix scales the two chains like the wheel does on Windows.
Shortcut: SHH/chat-boost/3D curves approximate Waves by ear; Waves' real filters are not in the binaries.
"""
import json, math, os, subprocess

# Same names Windows shows for the two endpoints (Waves driver): game audio and the virtual chat device
GAME_NAME = "Stealth 600 PC Gen 3"
CHAT_NAME = "Headset Chat (Turtle Beach)"
CACHE = os.path.expanduser("~/.cache/tb600")
# SADIE II subject D1 (Neumann KU100), 48 kHz 256-tap HRIRs, Apache-2.0, University of York (installed by
# install.sh). Replaced MIT KEMAR (1994, 44.1 kHz), whose measurement noise was audible as hiss.
HRTF = os.path.expanduser("~/.local/share/tb600/hrtf/sadie2_d1_ku100_48k.sofa")
HRIR_DIR = os.path.expanduser("~/.local/share/tb600/hrtf/hrir")  # per-angle WAVs made by tb600_hrir.py


CHANNELS = ["FL", "FR", "FC", "LFE", "RL", "RR", "SL", "SR"]  # TB Game is a 7.1 sink, like Windows' endpoint
# 3D off: ITU-style downmix to the two ears (L, R); FC/sides/rears at -3 dB, LFE at -6 dB
DOWNMIX = {"FL": (1, 0), "FR": (0, 1), "FC": (0.707, 0.707), "LFE": (0.5, 0.5),
           "RL": (0.707, 0), "RR": (0, 0.707), "SL": (0.707, 0), "SR": (0, 0.707)}


def hrir_scale():
    try:
        return float(open(os.path.join(HRIR_DIR, "scale.txt")).read())
    except (OSError, ValueError):
        return 1.0


def _game_nodes(preset):
    """7.1 in -> per-channel virtual speaker (SADIE II HRIR convolution) or plain downmix -> SHH EQ -> 2 ears."""
    n, links = [], []
    gain = hrir_scale()
    for ch in CHANNELS:
        n.append(f'{{ type = builtin name = in{ch} label = copy }}')
        if ch != "LFE":
            ir = os.path.join(HRIR_DIR, f"az{SURROUND_PRESETS[preset][ch] % 360:03d}.wav")
            for i, ear in enumerate("LR"):
                n.append(f'{{ type = builtin name = cv{ch}{ear} label = convolver config = {{ filename = "{ir}" '
                         f'channel = {i} gain = {gain} }} }}')
                links.append(f'{{ output = "in{ch}:Out" input = "cv{ch}{ear}:In" }}')
    spk = [c for c in CHANNELS if c != "LFE"]
    for e, ear in enumerate("LR"):
        n.append(f'{{ type = builtin name = dry{ear} label = mixer }}')  # In 1..8 = CHANNELS
        n.append(f'{{ type = builtin name = wet{ear} label = mixer }}')  # In 1..7 = speakers, In 8 = LFE
        n.append(f'{{ type = builtin name = sel{ear} label = mixer }}')  # In 1 = dry, In 2 = wet
        for i, ch in enumerate(CHANNELS, 1):
            links.append(f'{{ output = "in{ch}:Out" input = "dry{ear}:In {i}" }}')
        for i, ch in enumerate(spk, 1):
            links.append(f'{{ output = "cv{ch}{ear}:Out" input = "wet{ear}:In {i}" }}')
        links += [f'{{ output = "inLFE:Out" input = "wet{ear}:In 8" }}',
                  f'{{ output = "dry{ear}:Out" input = "sel{ear}:In 1" }}',
                  f'{{ output = "wet{ear}:Out" input = "sel{ear}:In 2" }}']
        n += [f'{{ type = builtin name = low{ear} label = bq_lowshelf control = {{ "Freq" = 250.0 "Q" = 0.7 "Gain" = 0.0 }} }}',
              f'{{ type = builtin name = shh{ear} label = bq_peaking control = {{ "Freq" = 3500.0 "Q" = 0.8 "Gain" = 0.0 }} }}',
              f'{{ type = builtin name = shhb{ear} label = bq_peaking control = {{ "Freq" = 6500.0 "Q" = 1.0 "Gain" = 0.0 }} }}',
              # compensation of the HRTF's timbre (active only with 3D on)
              f'{{ type = builtin name = dfl{ear} label = bq_lowshelf control = {{ "Freq" = 300.0 "Q" = 0.7 "Gain" = 0.0 }} }}',
              f'{{ type = builtin name = dfp{ear} label = bq_peaking control = {{ "Freq" = 3000.0 "Q" = 0.9 "Gain" = 0.0 }} }}']
        links += [f'{{ output = "sel{ear}:Out" input = "low{ear}:In" }}', f'{{ output = "low{ear}:Out" input = "shh{ear}:In" }}',
                  f'{{ output = "shh{ear}:Out" input = "shhb{ear}:In" }}', f'{{ output = "shhb{ear}:Out" input = "dfl{ear}:In" }}',
                  f'{{ output = "dfl{ear}:Out" input = "dfp{ear}:In" }}']
    return n, links, [f'"in{c}:In"' for c in CHANNELS], ['"dfpL:Out"', '"dfpR:Out"']


def _chat_nodes():
    n, links = [], []
    for c in "LR":
        n += [f'{{ type = builtin name = hp{c} label = bq_highpass control = {{ "Freq" = 20.0 "Q" = 0.7 }} }}',
              f'{{ type = builtin name = pres{c} label = bq_peaking control = {{ "Freq" = 3000.0 "Q" = 0.9 "Gain" = 0.0 }} }}',
              f'{{ type = builtin name = vol{c} label = linear control = {{ "Mult" = 1.0 "Add" = 0.0 }} }}']
        links += [f'{{ output = "hp{c}:Out" input = "pres{c}:In" }}', f'{{ output = "pres{c}:Out" input = "vol{c}:In" }}']
    return n, links, ['"hpL:In"', '"hpR:In"'], ['"volL:Out"', '"volR:Out"']


def _chain(name, desc, target, parts, positions=("FL", "FR")):
    nodes, links, ins, outs = parts
    return f"""  {{ name = libpipewire-module-filter-chain
    args = {{
      node.description = "{desc}"
      media.name = "{desc}"
      filter.graph = {{
        nodes = [ {' '.join(nodes)} ]
        links = [ {' '.join(links)} ]
        inputs = [ {' '.join(ins)} ]
        outputs = [ {' '.join(outs)} ]
      }}
      capture.props = {{ node.name = "{name}" media.class = Audio/Sink audio.channels = {len(positions)}
                         audio.position = [ {" ".join(positions)} ] }}
      playback.props = {{ node.name = "{name}_out" node.passive = true audio.channels = 2 audio.position = [ FL FR ]
                          target.object = "{target}" }}
    }}
  }}"""


def write_conf(target, preset="game"):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "dsp.conf")
    conf = f"""context.properties = {{ log.level = 2 }}
context.spa-libs = {{ audio.convert.* = audioconvert/libspa-audioconvert support.* = support/libspa-support }}
context.modules = [
  {{ name = libpipewire-module-rt flags = [ ifexists nofail ] }}
  {{ name = libpipewire-module-protocol-native }}
  {{ name = libpipewire-module-client-node }}
  {{ name = libpipewire-module-adapter }}
{_chain("tb600_game", GAME_NAME, target, _game_nodes(preset), CHANNELS)}
{_chain("tb600_chat", CHAT_NAME, target, _chat_nodes())}
]
"""
    open(path, "w").write(conf)
    return path


def _die_with_parent():
    # PR_SET_PDEATHSIG: the DSP must never outlive tb600d (an orphan duplicates TB Game/TB Chat)
    import ctypes, signal
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)


def start(target, preset="game"):
    if preset not in SURROUND_PRESETS:
        preset = "game"
    if not os.path.exists(os.path.join(HRIR_DIR, f"az{SURROUND_PRESETS[preset]['FL'] % 360:03d}.wav")):
        import tb600_hrir  # first run: cut the per-angle IRs out of the SOFA file
        tb600_hrir.extract(HRTF, {a % 360 for p in SURROUND_PRESETS.values() for a in p.values()}, HRIR_DIR)
    conf = write_conf(target, preset)
    subprocess.run(["pkill", "-f", f"^pipewire -c {conf}$"])  # leftovers from a crashed/killed run
    return subprocess.Popen(["pipewire", "-c", conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            preexec_fn=_die_with_parent)


def node_id(name):
    for o in json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True).stdout or "[]"):
        if o.get("type") == "PipeWire:Interface:Node" and o.get("info", {}).get("props", {}).get("node.name") == name:
            return o["id"]
    return None


def set_controls(name, params):
    nid = node_id(name)
    if nid is None:
        return False
    flat = " ".join(f'"{k}" {float(v)}' for k, v in params.items())
    subprocess.run(["pw-cli", "set-param", str(nid), "Props", f"{{ params = [ {flat} ] }}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def mix_gains(chat_mix):
    """0..100, 50 = balanced. Below 50 attenuates chat, above 50 attenuates game."""
    v = max(0, min(100, int(chat_mix)))
    return (1.0 if v <= 50 else (100 - v) / 50), (1.0 if v >= 50 else v / 50)


# Superhuman Hearing presets (SHH.810: 0 Default, 1 Footsteps, 2 Gunshots), as gain factors of the level:
# (low-shelf Hz, factor), (peak 1 Hz, Q, factor), (peak 2 Hz, Q, factor)
# shortcut: curves tuned by ear to Waves' intent (clear mids / footsteps / gunfire); Waves' filters aren't shipped
SHH_PRESETS = {
    0: ((250, -0.6), (3500, 0.8, 1.0), (6500, 1.0, 0.0)),   # Default: brighter mids, less rumble
    1: ((120, -0.8), (400, 1.0, 0.6), (3000, 1.2, 0.8)),    # Footsteps: step thump + scuff, cut explosions
    2: ((150, -0.6), (1800, 1.0, 0.7), (6500, 1.0, 0.6)),   # Gunshots: crack and tail of shots
}
# Waves 3D presets = virtual 7.1 speaker angles (degrees, SOFA convention: counter-clockwise, + = left)
SURROUND_PRESETS = {
    "game":  {"FL": 30, "FR": -30, "FC": 0, "SL": 90, "SR": -90, "RL": 135, "RR": -135},
    "movie": {"FL": 35, "FR": -35, "FC": 0, "SL": 100, "SR": -100, "RL": 145, "RR": -145},
    "music": {"FL": 25, "FR": -25, "FC": 0, "SL": 80, "SR": -80, "RL": 125, "RR": -125},
}
# HRTF timbre compensation (low-shelf dB, peak Hz, peak dB, peak Q), tuned by measuring pink noise
SURROUND_COMP = (-2.0, 1500.0, 3.0, 1.4)
SURROUND_GAIN = 10 ** (-10.5 / 20)  # measured: the HRIRs (mysofa loudness-normalised) came out 10.5 dB hot


def apply(st):
    """st: chat_mix, shh, shh_level, shh_preset, chat_boost, surround, surround_preset."""
    game, chat = mix_gains(st.get("chat_mix", 50))
    lvl = 9.0 * st.get("shh_level", 50) / 100 if st.get("shh") else 0.0
    (lf, lk), (p1, q1, k1), (p2, q2, k2) = SHH_PRESETS.get(st.get("shh_preset") or 0, SHH_PRESETS[0])
    preset = st.get("surround_preset") if st.get("surround_preset") in SURROUND_PRESETS else "game"
    on = bool(st.get("surround"))
    comp_low, comp_f, comp_peak, comp_q = SURROUND_COMP
    g = {}  # speaker angles are baked into the convolvers (start(preset)); tb600d restarts on preset change
    for e, ear in enumerate("LR"):
        for i, ch in enumerate(CHANNELS, 1):
            g[f"dry{ear}:Gain {i}"] = DOWNMIX[ch][e]
        for i, ch in enumerate([c for c in CHANNELS if c != "LFE"], 1):
            g[f"wet{ear}:Gain {i}"] = (0.707 if ch == "FC" else 1.0) * SURROUND_GAIN
        g[f"wet{ear}:Gain 8"] = 0.5  # LFE has no direction: straight to both ears
        g[f"sel{ear}:Gain 1"], g[f"sel{ear}:Gain 2"] = (0.0, game) if on else (game, 0.0)
    for c in "LR":
        g.update({f"low{c}:Freq": lf, f"low{c}:Gain": lvl * lk,
                  f"shh{c}:Freq": p1, f"shh{c}:Q": q1, f"shh{c}:Gain": lvl * k1,
                  f"shhb{c}:Freq": p2, f"shhb{c}:Q": q2, f"shhb{c}:Gain": lvl * k2,
                  f"dfl{c}:Gain": comp_low if on else 0.0, f"dfp{c}:Freq": comp_f, f"dfp{c}:Q": comp_q,
                  f"dfp{c}:Gain": comp_peak if on else 0.0})
    c_ = {}
    for c in "LR":
        # Chat Boost: gentle presence lift + rumble cut (a narrow +6 dB at 3 kHz sounded harsh)
        c_.update({f"hp{c}:Freq": 100.0 if st.get("chat_boost") else 20.0,
                   f"pres{c}:Freq": 2500.0, f"pres{c}:Q": 0.6,
                   f"pres{c}:Gain": 4.0 if st.get("chat_boost") else 0.0, f"vol{c}:Mult": chat})
    return set_controls("tb600_game", g) and set_controls("tb600_chat", c_)
