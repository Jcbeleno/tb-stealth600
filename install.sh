#!/bin/sh
# Installs TB Stealth 600 for the current user (no root needed except the udev rule).
set -e
D=$(cd "$(dirname "$0")" && pwd)
mkdir -p ~/.local/bin ~/.local/share/applications ~/.config/systemd/user

ln -sf "$D/tbctl.py" ~/.local/bin/tbctl
ln -sf "$D/tb600d.py" ~/.local/bin/tb600d
ln -sf "$D/tb600_gui.py" ~/.local/bin/tb600-gui
chmod +x "$D/tbctl.py" "$D/tb600d.py" "$D/tb600_gui.py"

cat > ~/.local/share/applications/dev.jucabego.TBStealth600.desktop <<EOF
[Desktop Entry]
Name=TB Stealth 600
Comment=Ajustes del Turtle Beach Stealth 600 Gen 3 (sustituto de Swarm II)
Exec=$HOME/.local/bin/tb600-gui
Icon=audio-headphones
Terminal=false
Type=Application
Categories=AudioVideo;Audio;Settings;
StartupWMClass=dev.jucabego.TBStealth600
EOF

cat > ~/.config/systemd/user/tb600d.service <<EOF
[Unit]
Description=TB Stealth 600 daemon (DSP + headset state)
After=pipewire.service pipewire-pulse.service wireplumber.service
Wants=pipewire.service

[Service]
ExecStart=$HOME/.local/bin/tb600d
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload

# Noctalia: local plugin source (directory name must match the id suffix "stealth600")
if command -v noctalia >/dev/null; then
  noctalia msg plugins source add tb-stealth600 path "$D/noctalia-plugins" 2>/dev/null || true
  noctalia msg plugins enable jucabego/stealth600 2>/dev/null || true
fi

[ -f /etc/udev/rules.d/70-tb-stealth600.rules ] || echo "Falta la regla udev: sudo install -m644 $D/70-tb-stealth600.rules /etc/udev/rules.d/"
echo "Listo. Servicio: systemctl --user enable --now tb600d"
