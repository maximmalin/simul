#!/usr/bin/env bash
# Дозагрузка недостающих 3D-моделей с pcb-3d.com — открытого каталога
# STEP-моделей для PCB. Регистрация и оплата не нужны.
#
# Формат скачивания:
#   https://www.pcb-3d.com/wordpress/models/<slug>
#     -> <form action=".../wp-content/uploads/step-free/<NAME>.STEP.zip">
# Ссылка лежит в HTML страницы, поэтому slug -> URL берём оттуда, а не
# угадываем имя файла (в именах есть точки, скобки и разные регистры).
#
# Это ЗАПАСНОЙ источник: основной — официальный packages3d (fetch_3dmodels.sh),
# он покрывает 18 из 23 корпусов. Здесь закрываются оставшиеся, причём
# не точными моделями, а геометрически эквивалентными: те же габариты
# посадочного места, другое изделие того же типоразмера.

set -euo pipefail

DEST="${MODELS_DIR:-/mnt/ext4data/kicad3d/9.0.2}"
PAGE="https://www.pcb-3d.com/wordpress/models"
UA="Mozilla/5.0 (X11; Linux x86_64)"

mkdir -p "$DEST"

# slug на pcb-3d.com  ->  куда кладём и под каким именем
# (имя соответствует ожидаемому корпусу KiCad, чтобы fetch_3dmodels.sh
#  и tscircuit нашли файл по стандартному пути <Lib>.3dshapes/<Name>.step)
declare -A MAP=(
  # JW A3963 — разъём 3.96 мм на 2 контакта, вертикальный.
  # Берём JST VH B2P: тот же шаг 3.96 мм, то же посадочное место.
  [jst_b2p-vhlfsn_hdr_th_3-96mm_pitch_v]="Connector.3dshapes/JWT_A3963_1x02_P3.96mm_Vertical.step"
  # NS-Tech Grove — разъём 2 мм на 4 контакта. Замены на сайте нет,
  # поэтому генерируется геометрически (см. gen_missing_models.py).
  # Coilcraft MSS1278H — силовой индуктор 12x12 мм. Наш L1 = 18 мкГн 12x12.
  [indp120120x780-coilcraft-mss1278h-series]="Inductor_SMD.3dshapes/L_12x12mm_H6mm.step"
  # Keystone 5015 — тест-точка для монтажа на плату.
  [test_point_keystone_5015_sm]="TestPoint.3dshapes/TestPoint_Pad_D1.0mm.step"
  # F1 = предохранитель 1812 (метрический типоразмер 4532).
  # Отдельной модели предохранителя 1812 на сайте нет, поэтому берём
  # конденсатор того же посадочного места: 450x320x218 mils = 4.5x3.2x0.55 мм,
  # а у корпуса Fuse_1812_4532Metric ровно 4.5x3.2 мм. Для 3D-рендера
  # и проверки зазоров это точная замена по габаритам.
  [capc4532x218-eia-1812-86-metric-4532-450x320x218x050]="Fuse.3dshapes/Fuse_1812_4532Metric.step"
)

for slug in "${!MAP[@]}"; do
  out="$DEST/${MAP[$slug]}"
  if [ -s "$out" ]; then
    printf '  %-8s %s (уже есть)\n' "есть" "${MAP[$slug]}"
    continue
  fi
  mkdir -p "$(dirname "$out")"

  url=$(curl -sSL -A "$UA" "$PAGE/$slug" 2>/dev/null \
        | grep -oiE 'action="[^"]*step-free[^"]*\.zip"' | head -1 \
        | sed 's/action="//; s/"$//')
  if [ -z "$url" ]; then
    printf '  %-8s %s  <- ссылка на STEP не найдена (slug=%s)\n' "НЕТ" "${MAP[$slug]}" "$slug"
    continue
  fi

  if curl -sSfL -A "$UA" --retry 2 -o "$out.zip" "$url" 2>/dev/null && [ -s "$out.zip" ]; then
    inner=$(unzip -Z1 "$out.zip" 2>/dev/null | grep -iE '\.(step|stp)$' | head -1)
    if [ -n "$inner" ]; then
      unzip -o -j -p "$out.zip" "$inner" > "$out" 2>/dev/null
      rm -f "$out.zip"
      if [ -s "$out" ]; then
        printf '  %-8s %s (%s байт)\n' "OK" "${MAP[$slug]}" "$(stat -c%s "$out")"
        continue
      fi
    fi
  fi
  rm -f "$out.zip"
  printf '  %-8s %s  <- архив не распакован (slug=%s)\n' "ОШИБКА" "${MAP[$slug]}" "$slug"
done

echo
echo "хранилище: $DEST"
find "$DEST" -name '*.step' | wc -l | xargs echo "всего STEP:"