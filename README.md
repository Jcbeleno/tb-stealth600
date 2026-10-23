# tb-stealth600

Linux control app for the **Turtle Beach Stealth 600 Gen 3 PC** headset (transmitter `10f5:224c`), a replacement for Swarm II.
The protocol was reverse-engineered from Swarm II.

- `tb600.py`: library (HID RACE + CoAP: settings, EQ, custom presets, backup/restore)
- `tbctl.py`: CLI (`tbctl show`, `tbctl set noise_gate on`, `tbctl eq game`, `tbctl watch`…)
- `tb600d.py`: user service: live state over CoAP Observe, PipeWire DSP (game/chat mix, Superhuman Hearing, Chat Boost, 7.1 virtual surround with the SADIE II HRTF), battery alerts
- `tb600_gui.py`: GTK4/libadwaita app in the Swarm II style
- `noctalia-plugins/stealth600`: battery widget for Noctalia

## Install

```sh
sudo install -m644 70-tb-stealth600.rules /etc/udev/rules.d/
./install.sh
systemctl --user enable --now tb600d
```

The 3D surround needs `~/.local/share/tb600/hrtf/sadie2_d1_ku100_48k.sofa` (SADIE II D1 KU100, Zenodo 10886409) and `libmysofa`.

Firmware updates are not implemented. Product photos in `assets/` © Turtle Beach.
