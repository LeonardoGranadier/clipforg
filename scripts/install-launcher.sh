#!/usr/bin/env bash
# Cria o atalho "ClipForge" no menu de aplicativos do Linux (só para o seu usuário).
# Para remover: rm ~/.local/share/applications/clipforge.desktop
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/.local/share/applications/clipforge.desktop"
mkdir -p "$(dirname "$DEST")"
cat > "$DEST" <<DESKTOP
[Desktop Entry]
Type=Application
Name=ClipForge
Comment=Cortes curtos com legendas a partir dos seus vídeos
Exec="$DIR/scripts/start.sh"
Icon=video-x-generic
Terminal=false
Categories=AudioVideo;Video;
DESKTOP
chmod +x "$DIR/scripts/start.sh"
echo "Atalho criado: procure 'ClipForge' no menu de aplicativos."
