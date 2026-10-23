#!/usr/bin/env python3
"""Extract the few HRIRs tb600_dsp needs from a SOFA file into small stereo WAVs (one per angle).

Loading a full SOFA inside PipeWire (the `sofa` spatializer) opens the file and builds its neighbour
search once per node; with SADIE II (35 MB, 8802 directions) and 7 virtual speakers that stalled the
whole audio graph. Static per-angle WAVs + the builtin convolver load instantly.
"""
import ctypes, math, os, struct, sys, wave

RATE = 48000


def extract(sofa, angles, outdir):
    lib = ctypes.CDLL("libmysofa.so.1")
    lib.mysofa_open.restype = ctypes.c_void_p
    lib.mysofa_open.argtypes = [ctypes.c_char_p, ctypes.c_float, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    lib.mysofa_getfilter_float.argtypes = [ctypes.c_void_p, ctypes.c_float, ctypes.c_float, ctypes.c_float,
                                           ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
                                           ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
    lib.mysofa_close.argtypes = [ctypes.c_void_p]
    n, err = ctypes.c_int(), ctypes.c_int()
    h = lib.mysofa_open(sofa.encode(), RATE, ctypes.byref(n), ctypes.byref(err))
    if not h:
        raise RuntimeError(f"mysofa_open failed ({err.value})")
    os.makedirs(outdir, exist_ok=True)
    irs = {}
    try:
        for az in sorted(set(angles)):
            left, right = (ctypes.c_float * n.value)(), (ctypes.c_float * n.value)()
            dl, dr = ctypes.c_float(), ctypes.c_float()
            r = math.radians(az)  # SOFA: x front, y left (counter-clockwise azimuth), z up
            lib.mysofa_getfilter_float(h, math.cos(r), math.sin(r), 0.0, left, right, ctypes.byref(dl), ctypes.byref(dr))
            pad_l, pad_r = round(dl.value * RATE), round(dr.value * RATE)  # SADIE embeds ITD (0 here)
            irs[az % 360] = ([0.0] * pad_l + list(left) + [0.0] * pad_r, [0.0] * pad_r + list(right) + [0.0] * pad_l)
    finally:
        lib.mysofa_close(h)
    # mysofa's loudness normalisation can exceed 1.0: scale every IR by one common factor (keeps the
    # level relations between angles) and let the convolver gain undo it (written to scale.txt)
    peak = max(abs(v) for L, R in irs.values() for v in L + R) * 1.001
    for az, (L, R) in irs.items():
        with wave.open(os.path.join(outdir, f"az{az:03d}.wav"), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(4)  # 32-bit PCM keeps the IR precision; the convolver reads it via libsndfile
            w.setframerate(RATE)
            w.writeframes(b"".join(struct.pack("<ii", round(a / peak * 2147483647), round(b / peak * 2147483647))
                                   for a, b in zip(L, R)))
    with open(os.path.join(outdir, "scale.txt"), "w") as f:
        f.write(f"{peak}\n")


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
    import tb600_dsp
    angles = {a % 360 for p in tb600_dsp.SURROUND_PRESETS.values() for a in p.values()}
    extract(tb600_dsp.HRTF, angles, tb600_dsp.HRIR_DIR)
    print(f"{len(angles)} HRIRs -> {tb600_dsp.HRIR_DIR}")
