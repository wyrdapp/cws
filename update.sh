#!/bin/bash
# Aktualizace pluginu a push na GitHub
set -e

PLUGIN_SRC="/home/petr/Dokumenty/Projekty/Stremio/kodi/plugin.video.webshare"
PLUGIN_ID="plugin.video.cws"
REPO_DIR="$(dirname "$0")/repo"

# Přečti verzi z addon.xml zdrojového pluginu
VERSION=$(grep -oP 'version="\K[^"]+' "$PLUGIN_SRC/addon.xml" | head -1)
echo "Verze: $VERSION"

# Zkopíruj soubory pluginu (bez ZIP a cache)
rsync -av --exclude='*.pyc' --exclude='__pycache__' --exclude='*.zip' \
    "$PLUGIN_SRC/" "$REPO_DIR/$PLUGIN_ID/"

# Přepiš addon.xml naším CWS variantou
cp "$REPO_DIR/$PLUGIN_ID/addon.xml.cws" "$REPO_DIR/$PLUGIN_ID/addon.xml" 2>/dev/null || true

# Vytvoř nový ZIP
cd "$REPO_DIR/$PLUGIN_ID"
zip -r "../$PLUGIN_ID/$PLUGIN_ID-$VERSION.zip" . \
    -x '*.pyc' -x '__pycache__/*' -x '*.zip' -x 'addon.xml.cws'

# Přegeneruj addons.xml + md5
cd "$REPO_DIR"
python3 - <<'EOF'
import os, re

addons_xml = '<?xml version="1.0" encoding="UTF-8"?>\n<addons>\n\n'
for d in sorted(os.listdir('.')):
    axml = os.path.join(d, 'addon.xml')
    if os.path.isfile(axml):
        with open(axml, encoding='utf-8') as f:
            content = f.read()
        # Odstraň XML hlavičku
        content = re.sub(r'<\?xml[^>]+\?>\s*', '', content)
        addons_xml += content.strip() + '\n\n'
addons_xml += '</addons>\n'

with open('addons.xml', 'w', encoding='utf-8') as f:
    f.write(addons_xml)
print("addons.xml OK")
EOF

md5sum addons.xml > addons.xml.md5
echo "addons.xml.md5 OK"

# Git commit a push
cd "$(dirname "$0")"
git add -A
git commit -m "Update $PLUGIN_ID to $VERSION"
git push
echo "Hotovo – verze $VERSION je na GitHubu."
