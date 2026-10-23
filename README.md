# tb-stealth600

Linux control app for the **Turtle Beach Stealth 600 Gen 3 PC** headset (transmitter `10f5:224c`), a replacement for Swarm II.
The protocol was reverse-engineered from Swarm II.

- `tb600.py`: library (HID RACE + CoAP: settings, EQ, custom presets, backup/restore)
- `tbctl.py`: CLI (`tbctl show`, `tbctl set noise_gate on`, `tbctl eq game`, `tbctl watch`…)
- `tb600d.py`: user service: live state over CoAP Observe, PipeWire DSP (game/chat mix, Superhuman Hearing, Chat Boost, 7.1 virtual surround with the SADIE II HRTF), battery alerts
- `tb600_gui.py`: GTK4/libadwaita app in the Swarm II style
- `noctalia-plugins/stealth600`: battery widget for Noctalia

## Install

One command on any distro with systemd and PipeWire (Arch/CachyOS, Debian 13, Ubuntu 24.04+, Fedora 40+, openSUSE):

```sh
curl -fsSL https://raw.githubusercontent.com/Jcbeleno/tb-stealth600/main/install.sh | bash
```

It installs the missing packages, the udev rule (sudo), the app in `~/.local/share/tb-stealth600`, the launcher and
the `tb600d` user service. Run it again to update; `install.sh --uninstall` removes everything.
From a checkout, `./install.sh` installs that folder in place.

Requires GTK ≥ 4.12 and libadwaita ≥ 1.5. The 3D surround uses HRIRs from SADIE II (Apache-2.0, see `assets/hrir/NOTICE`).

Firmware updates are not implemented. The headset photos are © Turtle Beach: they are not in this repo, the installer downloads them from the Turtle Beach store (`tb600_views.py`).
