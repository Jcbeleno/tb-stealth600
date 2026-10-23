#!/usr/bin/env bash
# TB Stealth 600 installer: one command, any systemd + PipeWire distro (Arch, Debian/Ubuntu, Fedora, openSUSE).
#   curl -fsSL https://raw.githubusercontent.com/Jcbeleno/tb-stealth600/main/install.sh | bash
#   ./install.sh               from a checkout (installs that checkout in place)
#   ./install.sh --uninstall
# Installs per user under ~/.local; sudo is only used for system packages and the udev rule.
set -euo pipefail

REPO=https://github.com/Jcbeleno/tb-stealth600
APP=$HOME/.local/share/tb-stealth600
BIN=$HOME/.local/bin
UNIT=$HOME/.config/systemd/user/tb600d.service
DESKTOP=$HOME/.local/share/applications/dev.jucabego.TBStealth600.desktop
RULE=/etc/udev/rules.d/70-tb-stealth600.rules

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }
sudo_() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

if [ "${1:-}" = "--uninstall" ]; then
  systemctl --user disable --now tb600d 2>/dev/null || true  # the daemon restores the default sink and volume on exit
  rm -f "$UNIT" "$DESKTOP" "$BIN/tbctl" "$BIN/tb600d" "$BIN/tb600-gui"
  systemctl --user daemon-reload
  [ -f "$RULE" ] && sudo_ rm -f "$RULE" && sudo_ udevadm control --reload
  rm -rf "$APP"
  say "Desinstalado. Los ajustes siguen en ~/.config/tb600 y en el propio headset."
  exit 0
fi

[ "$(id -u)" = 0 ] && die "ejecútalo como tu usuario, no como root (usa sudo solo cuando lo pida)."
command -v systemctl >/dev/null || die "hace falta systemd."

# --- source: this checkout, or download into ~/.local/share/tb-stealth600 ---
SRC=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)
if [ -n "$SRC" ] && [ -f "$SRC/tb600.py" ]; then
  APP=$SRC
elif [ -d "$APP/.git" ]; then
  say "Actualizando $APP"
  git -C "$APP" pull --ff-only
else
  say "Descargando en $APP"
  command -v git >/dev/null || die "hace falta git para descargar ($REPO)."
  git clone --depth 1 "$REPO" "$APP"
fi

# --- system packages (only what is missing) ---
deps_ok() {
  python3 - <<'EOF' 2>/dev/null
import gi
gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0"); gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gtk
assert (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 12), "gtk"
assert (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5), "adw"
EOF
  [ $? = 0 ] && command -v pactl >/dev/null && command -v pw-cli >/dev/null && command -v notify-send >/dev/null
}
if ! deps_ok; then
  say "Instalando dependencias (pedirá tu contraseña)"
  if command -v pacman >/dev/null; then
    sudo_ pacman -S --needed --noconfirm python python-gobject gtk4 libadwaita pipewire pipewire-pulse libpulse libnotify
  elif command -v apt-get >/dev/null; then
    sudo_ apt-get update -q
    sudo_ apt-get install -y python3 python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-graphene-1.0 \
      pipewire pipewire-pulse pipewire-bin pulseaudio-utils libnotify-bin
  elif command -v dnf >/dev/null; then
    sudo_ dnf install -y python3 python3-gobject gtk4 libadwaita pipewire pipewire-pulseaudio pipewire-utils \
      pulseaudio-utils libnotify
  elif command -v zypper >/dev/null; then
    sudo_ zypper --non-interactive install python3 python3-gobject python3-gobject-Gdk typelib-1_0-Gtk-4_0 \
      typelib-1_0-Adw-1 pipewire pipewire-pulseaudio pipewire-tools pulseaudio-utils libnotify-tools
  else
    die "distribución no reconocida: instala Python 3 + PyGObject, GTK ≥ 4.12, libadwaita ≥ 1.5, PipeWire (pipewire-pulse) y notify-send."
  fi
  deps_ok || die "las dependencias siguen sin cumplirse: hace falta GTK ≥ 4.12 y libadwaita ≥ 1.5 (Ubuntu 24.04, Debian 13, Fedora 40 o más recientes)."
fi
pactl info 2>/dev/null | grep -q PipeWire || die "el audio no corre sobre PipeWire (pactl info no lo muestra)."

# --- udev: let the logged-in user open the transmitter's hidraw node ---
if ! cmp -s "$APP/70-tb-stealth600.rules" "$RULE" 2>/dev/null; then
  say "Instalando la regla udev (pedirá tu contraseña)"
  sudo_ install -m644 "$APP/70-tb-stealth600.rules" "$RULE"
  sudo_ udevadm control --reload
  sudo_ udevadm trigger --subsystem-match=hidraw --action=change
fi

# --- product photos (Turtle Beach's, not in the repo): fetched from their store once ---
if [ ! -f "$APP/assets/views/audio.png" ]; then
  say "Descargando las fotos del headset"
  python3 "$APP/tb600_views.py" >/dev/null || say "No se pudieron descargar las fotos; la app funcionará sin ellas."
fi

# --- user files ---
say "Instalando la app"
mkdir -p "$BIN" "$(dirname "$DESKTOP")" "$(dirname "$UNIT")"
chmod +x "$APP/tbctl.py" "$APP/tb600d.py" "$APP/tb600_gui.py"
ln -sf "$APP/tbctl.py" "$BIN/tbctl"
ln -sf "$APP/tb600d.py" "$BIN/tb600d"
ln -sf "$APP/tb600_gui.py" "$BIN/tb600-gui"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Name=TB Stealth 600
Comment=Ajustes del Turtle Beach Stealth 600 Gen 3 (sustituto de Swarm II)
Exec=$BIN/tb600-gui
Icon=$APP/assets/views/audio.png
Terminal=false
Type=Application
Categories=AudioVideo;Audio;Settings;
StartupWMClass=dev.jucabego.TBStealth600
EOF

cat > "$UNIT" <<EOF
[Unit]
Description=TB Stealth 600 daemon (DSP + headset state)
After=pipewire.service pipewire-pulse.service wireplumber.service
Wants=pipewire.service

[Service]
ExecStart=$BIN/tb600d
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable tb600d >/dev/null 2>&1
systemctl --user restart tb600d

# Noctalia: local plugin source (directory name must match the id suffix "stealth600")
if command -v noctalia >/dev/null; then
  noctalia msg plugins source add tb-stealth600 path "$APP/noctalia-plugins" 2>/dev/null || true
  noctalia msg plugins enable jucabego/stealth600 2>/dev/null || true
fi

case ":$PATH:" in *":$BIN:"*) ;; *) say "Añade ~/.local/bin a tu PATH para usar «tbctl» en la terminal." ;; esac
say "Listo. Abre «TB Stealth 600» desde el menú de aplicaciones (o ejecuta tb600-gui)."
