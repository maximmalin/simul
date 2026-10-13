# Агенты (per-task prompts) — Krystal Radio

Каждый агент должен иметь возможность выполнить свою задачу с минимальным
контекстом: открыть репо, прочитать этот файл (или соответствующий раздел)
и начать работу. Структура: `ID | Название | Цель | Ключевые файлы | Команда`.

## 1. Makefile и цепочка сборки
- **ID**: `AGENT-MK-01`
- **Название**: Запустить `make all` и проверить RC=0
- **Цель**: Убедиться, что все этапы (netlist, pcb, drawings, gnuradio, page, tests) проходят без ошибок.
- **Ключевые файлы**: `Makefile`, `tools/build_page.py`, `tools/dump_manifest.py`
- **Команда**: `cd /mnt/ext4data/kristall-radio && make all 2>&1 | tail -5`

- **ID**: `AGENT-MK-02`
- **Название**: Проверить, что `make page` генерирует HTML с SVG-рисунками
- **Цель**: Подтвердить, что страница одной страницы (3D + GOST + waterfall) собирается корректно.
- **Ключевые файлы**: `tools/build_page.py`, `out/krystal-radio.html`
- **Команда**: `cd /mnt/ext4data/kristall-radio && python3 tools/build_page.py --out out/krystal-radio.html && grep -c 'data:image/svg+xml;base64' out/krystal-radio.html`

- **ID**: `AGENT-MK-03`
- **Название**: Обновить `link_budget_section()` в `tools/build_page.py`
- **Цель**: Убедиться, что раздел link budget на странице содержит таблицу каналов A/B/C с дальностью и примечанием о шумовой дорожке.
- **Ключевые файлы**: `tools/build_page.py` (функция `link_budget_section`)
- **Команда**: `cd /mnt/ext4data/kristall-radio && python3 -c "import tools.build_page as bp; print(bp.link_budget_section({'channels':[{'title':'zмейка 200 МГц','f_mhz':200,'g_rx_dbi':-3,'range_free_space_m':9821000,'range_realistic_m':982100,'noise_floor_pass':True,'mds_pass':True,'eirp_dbm':30}]}, {'eirp_dbm':30,'bw_hz':2700,'nf_db':3.5}))" | grep -c 'Дальность'`

## 2. Тесты требований (requirements tests)
- **ID**: `AGENT-TST-01`
- **Название**: Запуститьpytest tests/requirements и убедиться, что все 169 тестов проходят
- **Цель**: Подтвердить, что все суеты (EMC, reception, reliability, thermal, netlist, pin-contract) зеленые.
- **Ключевые файлы**: `tests/requirements/`, `tests/requirements/report/`, `tests/requirements/emc/`
- **Команда**: `cd /mnt/ext4data/kristall-radio && timeout 900 pytest tests/requirements -q`

- **ID**: `AGENT-TST-02`
- **Название**: Добавить новые тесты в `tests/requirements/report/`
- **Цель**: Добавить проверки наличия разделов «дальность приёма» и «пеленгация» на HTML-странице.
- **Ключевые файлы**: `tests/requirements/report/test_report_page.py`
- **Команда**: `cd /mnt/ext4data/kristall-radio && python3 -c "
from tests.requirements.report.test_report_page import *
import re
html = open('out/krystal-radio.html').read()
test_page_has_reception_distance(html)
test_page_has_direction_finding(html)
print('reception+direction tests pass')
"`

## 3. GNU Radio co-simulation
- **ID**: `AGENT-GNUR-01`
- **Название**: Сгенерировать водопад (waterfall.png) и спектр (virtual_rx_spectrum.json)
- **Цель**: Убедиться, что `make gnuradio` запускает GNU Radio co-simulation и сохраняет PNG + JSON.
- **Ключевые файлы**: `src/gnuradio_krystal/krystal_virtual_rx.py`, `out/waterfall.png`, `out/virtual_rx_spectrum.json`
- **Команда**: `cd /mnt/ext4data/kristall-radio && python3 src/gnuradio_krystal/krystal_virtual_rx.py --out out --nsamples 262144`

## 4. Thermal & Reliability
- **ID**: `AGENT-TH-01`
- **Название**: Запустить `make thermal` и проверить `out/thermal_{fast,fem}.json`
- **Цель**: Подтвердить, что Tj ≤ 85 °C (консервативно) и FEM-модель даёт Tmax ≤ 35 °C.
- **Ключевые файлы**: `sim/scripts/thermal_pcb.py`, `out/thermal_fast.json`, `out/thermal_fem.json`
- **Команда**: `cd /mnt/ext4data/kristall-radio && make thermal && cat out/thermal_fast.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"Tj_max\",d.get(\"Tj_max\"))'`

- **ID**: `AGENT-RL-01`
- **Название**: Запустить `make reliability` и проверить `out/reliability.json` (MTBF ≥ 10 000 ч)
- **Цель**: Подтвердить Octave parts-count + Arrhenius расчёт.
- **Ключевые файлы**: `sim/octave/reliability_model.m`, `sim/octave/reliability_parts.tsv`, `out/reliability.json`
- **Команда**: `cd /mnt/ext4data/kristall-radio && make reliability && cat out/reliability.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"MTBF ч\", d.get(\"MTBF_h\"))'`

## 5. Direction finding & AntennaSim
- **ID**: `AGENT-DF-01`
- **Название**: Запустить `make direction` и проверить `out/direction_finding.{json,svg}`
- **Цель**: Подтвердить, что phase interferometer считает θ̂ и рисует диаграмму.
- **Ключевые файлы**: `sim/scripts/direction_finding.py`, `out/direction_finding.json`, `out/direction_finding.svg`
- **Команда**: `cd /mnt/ext4data/kristall-radio && make direction`

- **ID**: `AGENT-DF-02`
- **Название**: Экспорт `.nec` для AntennaSim и проверка import
- **Цель**: Убедиться, что `out/df_array.nec` валиден и можно отправить на AntennaSim API.
- **Ключевые файлы**: `sim/scripts/direction_finding.py` (функция `write_nec_array`), `out/df_array.nec`
- **Команда**: `cd /mnt/ext4data/kristall-radio && python3 -c "open('out/df_array.nec').read(); print('NEC deck valid')"` 

## 6. YOLO ML pipeline (signal recognition)
- **ID**: `AGENT-ML-01`
- **Название**: Сгенерировать синтетический датасет YOLO
- **Цель**: Создать `ml_data/` с images/labels train/val и `data.yaml`, `scenes.json`.
- **Ключевые файлы**: `ml/dataset.py`, `ml/scene.py`, `ml/detector.py`
- **Команда**: `cd /mnt/ext4data/kristall-radio && python3 -c "from ml.dataset import generate_dataset; ds=generate_dataset('ml_data', n_train=16, n_val=4, seed=2025); print('DS root:', ds['root'])"`

- **ID**: `AGENT-ML-02`
- **Название**: Обучить компактную YOLO-сеть и сохранить артефакты
- **Цель**: Вызвать `ml.train.train()` → получить `model.json` + `model_weights.h5` и mAP@0.5.
- **Ключевые файлы**: `ml/train.py`, `model.json`, `model_weights.h5`
- **Команда**: `cd /mnt/ext4data/kristall-radio && python3 -c "from ml.train import train; r=train(epochs=6); print('mAP05:', r['mAP05']); print('model:', r['model_path'])"`

- **ID**: `AGENT-ML-03`
- **Название**: Инференс: дан PNG-спектрограмма, вывести список детектированных сигналов
- **Цель**: Получить список {cls, cx, cy, w, h} для входного изображения.
- **Ключевые файлы**: `ml/detector.py`, `detect_from_png()`
- **Команда**: `cd /mnt/ext4data/kristall-radio && python3 -c "
from ml.detector import detect_from_png, build_yolo_model
model = build_yolo_model(); detected, peak = detect_from_png('ml_data/images/train/00000.png', model)
print('detected:', [d.cls for d in detected]); print('peak:', peak)
"`

## 7. Документация и релизные ноты
- **ID**: `AGENT-DOC-01`
- **Название**: Обновить `README.md` с новыми make-целями (link-budget, direction, EMC, reception)
- **Цель**: Пользователь видит полный список доступных целей make.
- **Ключевые файлы**: `README.md`
- **Команда**: `cd /mnt/ext4data/kristall-radio && grep -A2 '--- Reception' README.md | head -6`

- **ID**: `AGENT-DOC-02`
- **Название**: Обновить `AGENT_MAP.md` с новыми командами агентов
- **Цель**: Новые агенты видят, какие цели доступны.
- **Ключевые файлы**: `AGENT_MAP.md`
- **Команда**: `cd /mnt/ext4data/kristall-radio && grep -A3 '--- Reception' AGENT_MAP.md | head -6`

- **ID**: `AGENT-DOC-03`
- **Название**: Добавить записи в `tests/requirements/checklist.csv` (EMC REQ-21..25 + reception + direction)
- **Цель**: Чеклист отражает выполнение всех требований.
- **Ключевые файлы**: `tests/requirements/checklist.csv`
- **Команда**: `cd /mnt/ext4data/kristall-radio && head -5 tests/requirements/checklist.csv && wc -l tests/requirements/checklist.csv`

## 8. Git & Release
- **ID**: `AGENT-GIT-01`
- **Название**: Подготовить коммит: пронуmeros все изменённые файлы, проверить `git status`
- **Цель**: Убедиться, что нет незакомitted изменений перед пушем.
- **Ключевые файлы**: `git status`, `.gitignore`
- **Команда**: `cd /mnt/ext4data/kristall-radio && git status --short | wc -l`

- **ID**: `AGENT-GIT-02`
- **Название**: Сделать `git commit -am "finish krystal-radio ...` и `git push origin main`
- **Цель**: Опубликовать финальный проект.
- **Ключевые файлы**: —, репозиторий `origin/main`
- **Команда**: `cd /mnt/ext4data/kristall-radio && git add -A && git commit -m "krystal-radio: finish SDR, YOLO ML, EMC, direction, page" && git push origin main`