#!/usr/bin/env bash
# Зеркалирование 3D-моделей корпусов из ОФИЦИАЛЬНОГО репозитория KiCad
# packages3d в локальное хранилище. Один раз — и дальше всё работает оффлайн.
#
# Источник: https://gitlab.com/kicad/libraries/kicad-packages3d
# Тег 9.0.2 соответствует установленному KiCad 9.0.2.
#
# Почему не tscircuit CDN: model_step_url, который генерирует tscircuit,
# указывает на kicad-mod-cache.tscircuit.com, а его использовать нельзя.
# Тег/path ниже соответствует ровно тому, что прописано в .kicad_mod:
#   ${KICAD9_3DMODEL_DIR}/<Lib>.3dshapes/<Name>.step
#
# Почему не apt install kicad-packages3d: пакет занимает 5 ГБ на диске,
# а свободно только 3.9 ГБ. Нужны 23 файла, а не 10000.
#
# Использование: hardware/tscircuit/fetch_3dmodels.sh [tag]

set -euo pipefail

TAG="${1:-9.0.2}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
NETLIST="$ROOT/hardware/kicad/schematic.net"
DEST="${MODELS_DIR:-/mnt/ext4data/kicad3d/$TAG}"
BASE="https://gitlab.com/kicad/libraries/kicad-packages3d/-/raw/$TAG"

mkdir -p "$DEST"

echo "Зеркалирование 3D-моделей KiCad packages3d, тег $TAG"
echo "  в:  $DEST"
echo "  из: $BASE"
echo

# Список корпусов берём из НЕТЛИСТА, а не из кода: нетлист выгружен
# самим KiCad, значит источник истины один.
mapfile -t FPS < <(
  grep -o '(footprint "[^"]*")' "$NETLIST" \
    | sed 's/(footprint "//; s/")//' \
    | sort -u
)

FP_ROOT="${KICAD_FP_ROOT:-/usr/share/kicad/footprints}"

echo "уникальных корпусов в нетлисте: ${#FPS[@]}"
echo

ok=0; miss=0
for fp in "${FPS[@]}"; do
  lib="${fp%%:*}"; name="${fp##*:}"
  mod="$FP_ROOT/$lib.pretty/$name.kicad_mod"

  # Путь к модели берём ИЗ САМОГО корпуса, а не строим по имени.
  # Это принципиально: у SOT-223-3_TabPin2.kicad_mod модель называется
  # SOT-223.step, и попытка угадать имя по названию корпуса даёт 404.
  if [ -f "$mod" ]; then
    rel=$(grep -o 'KICAD9_3DMODEL_DIR}/[^"]*\.step' "$mod" | head -1 \
          | sed 's|KICAD9_3DMODEL_DIR}/||')
  else
    rel=""
  fi

  if [ -z "$rel" ]; then
    printf '  %-8s %s  <- в корпусе нет ссылки на 3D-модель\n' "НЕТ" "$name"
    miss=$((miss+1))
    continue
  fi

  out="$DEST/$rel"
  mkdir -p "$(dirname "$out")"
  if [ -s "$out" ]; then
    printf '  %-8s %-42s (уже есть)\n' "есть" "$rel"
    ok=$((ok+1)); continue
  fi

  if curl -sSfL --retry 3 --connect-timeout 20 -o "$out.tmp" "$BASE/$rel" 2>/dev/null \
     && [ -s "$out.tmp" ]; then
    mv "$out.tmp" "$out"
    printf '  %-8s %-42s (%s байт)\n' "OK" "$rel" "$(stat -c%s "$out")"
    ok=$((ok+1))
  else
    rm -f "$out.tmp"
    printf '  %-8s %-42s  <- нет в packages3d\n' "НЕТ" "$rel"
    miss=$((miss+1))
  fi
done

echo
echo "скачано $ok, отсутствует $miss"
echo "хранилище: $DEST"
[ "$miss" -eq 0 ]