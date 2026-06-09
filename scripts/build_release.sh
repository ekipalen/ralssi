#!/usr/bin/env bash
# Rakenna ralssi-data.zip GitHub-releaseä varten ja (valinnaisesti) julkaise se.
# Sisältö = data/funding.db + per-source embeddingit + raakalähteet (verify tarvitsee iati/ + okm/).
# Polut zipissä ovat "data/..." koska `ralssi.py setup` purkaa zipin repon juureen (SCRIPT_DIR).
#
# Käyttö:
#   scripts/build_release.sh                 # rakenna zip data/ralssi-data.zip
#   scripts/build_release.sh vX.Y "otsikko"  # rakenna + gh release create (kysyy ennen uploadia)
set -euo pipefail
cd "$(dirname "$0")/.."          # repon juuri

DB="data/funding.db"
ZIP="data/ralssi-data.zip"

[ -f "$DB" ] || { echo "VIRHE: $DB puuttuu"; exit 1; }
echo "[build] integrity_check..."
if [ "$(sqlite3 "$DB" 'PRAGMA integrity_check' | head -1)" != "ok" ]; then
  echo "VIRHE: funding.db integrity_check epäonnistui"; exit 1
fi

rm -f "$ZIP"
echo "[build] paketoidaan -> $ZIP"
# Mukaan: db, embeddingit (.npy + *embedding_ids.json), raakatiedostot (xlsx/xlsm) ja hakemistot iati/okm/helsinki.
# Pois: .bak*, vanhat zipit, ralssi.db, WAL/SHM.
zip -r -q "$ZIP" \
    "$DB" \
    data/*.npy \
    data/*embedding_ids.json \
    data/*.xlsx data/*.xlsm \
    data/iati data/okm data/helsinki \
    -x 'data/*.bak*' 'data/*.zip' 'data/ralssi.db' 'data/funding.db-*'

echo "[build] valmis:"
ls -lh "$ZIP"
echo "[build] sisältö (top-level):"
unzip -l "$ZIP" | awk 'NR>3 {print $4}' | grep -v '^$' | sed 's#/.*#/#' | sort -u

if [ "${1:-}" != "" ]; then
  TAG="$1"; TITLE="${2:-ralssi data $TAG}"
  echo
  read -r -p "[release] Luodaanko GitHub-release $TAG ja ladataanko $ZIP? [y/N] " ans
  if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
    gh release create "$TAG" "$ZIP#ralssi-data.zip" --title "$TITLE" --notes-file -
  else
    echo "[release] ohitettu. Manuaalisesti: gh release create $TAG \"$ZIP#ralssi-data.zip\" --title \"$TITLE\""
  fi
fi
