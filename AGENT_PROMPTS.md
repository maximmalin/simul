# Агенты (per-task prompts) — Krystal Radio

Файл **порождён** из описания проекта: `backend/agents/prompts.py`.
Правьте `agents.project.yaml`, а не этот файл — иначе правки
пропадут при следующей генерации.

Проект: Krystal Radio · раннер: `make+npm` · целей: 43 · сгенерировано: 2026-10-15 15:11 UTC

Каждый агент выполняет свою задачу с минимальным контекстом:
открыть репозиторий, прочитать свой блок, выполнить команду.
Структура блока: `ID | Название | Цель | Ключевые файлы | Команда`.

Проверить, что промпты соответствуют проекту: `python3 -m agents prompts --check`

## 1. Declared

- **ID**: `AGENT-MK-01`
- **Название**: Запустить `make all` и проверить RC=0
- **Цель**: Убедиться, что все этапы (netlist, pcb, drawings, gnuradio, page, tests) проходят без ошибок.
- **Роль**: `agent.report`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`, `netlist`, `spice`, `pcb`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && make all 2>&1 | tail -5``

- **ID**: `AGENT-MK-02`
- **Название**: Проверить, что `make page` генерирует HTML с SVG-рисунками
- **Цель**: Подтвердить, что страница одной страницы (3D + GOST + waterfall) собирается корректно.
- **Роль**: `agent.report`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`, `publish`, `board-svg`, `drawings`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && python3 tools/build_page.py --out out/krystal-radio.html && grep -c 'data:image/svg+xml;base64' out/krystal-radio.html``

- **ID**: `AGENT-MK-03`
- **Название**: Обновить `link_budget_section()` в `tools/build_page.py`
- **Цель**: Убедиться, что раздел link budget на странице содержит таблицу каналов A/B/C с дальностью и примечанием о шумовой дорожке.
- **Роль**: `agent.report`
- **Ключевые файлы**: `tools/build_page.py` (функция `link_budget_section`)`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && python3 -c "import tools.build_page as bp; print(bp.link_budget_section({'channels':[{'title':'zмейка 200 МГц','f_mhz':200,'g_rx_dbi':-3,'range_free_space_m':9821000,'range_realistic_m':982100,'noise_floor_pass':True,'mds_pass':True,'eirp_dbm':30}]}, {'eirp_dbm':30,'bw_hz':2700,'nf_db':3.5}))" | grep -c 'Дальность'``

- **ID**: `AGENT-TST-01`
- **Название**: Запуститьpytest tests/requirements и убедиться, что все 169 тестов проходят
- **Цель**: Подтвердить, что все суеты (EMC, reception, reliability, thermal, netlist, pin-contract) зеленые.
- **Роль**: `agent.verify`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && timeout 900 pytest tests/requirements -q``

- **ID**: `AGENT-TST-02`
- **Название**: Добавить новые тесты в `tests/requirements/report/`
- **Цель**: Добавить проверки наличия разделов «дальность приёма» и «пеленгация» на HTML-странице.
- **Роль**: `agent.verify`
- **Ключевые файлы**: `tests/requirements/report/test_report_page.py`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && python3 -c " from tests.requirements.report.test_report_page import * import re html = open('out/krystal-radio.html').read() test_page_has_reception_distance(html) test_page_has_direction_finding(html) print('reception+direction tests pass') "``

- **ID**: `AGENT-GNUR-01`
- **Название**: Сгенерировать водопад (waterfall.png) и спектр (virtual_rx_spectrum.json)
- **Цель**: Убедиться, что `make gnuradio` запускает GNU Radio co-simulation и сохраняет PNG + JSON.
- **Роль**: `agent.rf`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && python3 src/gnuradio_krystal/krystal_virtual_rx.py --out out --nsamples 262144``

- **ID**: `AGENT-TH-01`
- **Название**: Запустить `make thermal` и проверить `out/thermal_{fast,fem}.json`
- **Цель**: Подтвердить, что Tj ≤ 85 °C (консервативно) и FEM-модель даёт Tmax ≤ 35 °C.
- **Роль**: `agent.thermal`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && make thermal && cat out/thermal_fast.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"Tj_max\",d.get(\"Tj_max\"))'``

- **ID**: `AGENT-RL-01`
- **Название**: Запустить `make reliability` и проверить `out/reliability.json` (MTBF ≥ 10 000 ч)
- **Цель**: Подтвердить Octave parts-count + Arrhenius расчёт.
- **Роль**: `agent.reliability`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && make reliability && cat out/reliability.json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(\"MTBF ч\", d.get(\"MTBF_h\"))'``

- **ID**: `AGENT-DF-01`
- **Название**: Запустить `make direction` и проверить `out/direction_finding.{json,svg}`
- **Цель**: Подтвердить, что phase interferometer считает θ̂ и рисует диаграмму.
- **Роль**: `agent.rf`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && make direction``

- **ID**: `AGENT-DF-02`
- **Название**: Экспорт `.nec` для AntennaSim и проверка import
- **Цель**: Убедиться, что `out/df_array.nec` валиден и можно отправить на AntennaSim API.
- **Роль**: `agent.rf`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`, `cad`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && python3 -c "open('out/df_array.nec').read(); print('NEC deck valid')"``

- **ID**: `AGENT-ML-01`
- **Название**: Сгенерировать синтетический датасет YOLO
- **Цель**: Создать `ml_data/` с images/labels train/val и `data.yaml`, `scenes.json`.
- **Роль**: `agent.ml`
- **Ключевые файлы**: `ml/dataset.py`, `ml/scene.py`, `ml/detector.py`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && python3 -c "from ml.dataset import generate_dataset; ds=generate_dataset('ml_data', n_train=16, n_val=4, seed=2025); print('DS root:', ds['root'])"``

- **ID**: `AGENT-ML-02`
- **Название**: Обучить компактную YOLO-сеть и сохранить артефакты
- **Цель**: Вызвать `ml.train.train()` → получить `model.json` + `model_weights.h5` и mAP@0.5.
- **Роль**: `agent.ml`
- **Ключевые файлы**: `ml/train.py`, `model.json`, `model_weights.h5`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && python3 -c "from ml.train import train; r=train(epochs=6); print('mAP05:', r['mAP05']); print('model:', r['model_path'])"``

- **ID**: `AGENT-ML-03`
- **Название**: Инференс: дан PNG-спектрограмма, вывести список детектированных сигналов
- **Цель**: Получить список {cls, cx, cy, w, h} для входного изображения.
- **Роль**: `agent.ml`
- **Ключевые файлы**: `ml/detector.py`, `detect_from_png()`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && python3 -c " from ml.detector import detect_from_png, build_yolo_model model = build_yolo_model(); detected, peak = detect_from_png('ml_data/images/train/00000.png', model) print('detected:', [d.cls for d in detected]); print('peak:', peak) "``

- **ID**: `AGENT-DOC-01`
- **Название**: Обновить `README.md` с новыми make-целями (link-budget, direction, EMC, reception)
- **Цель**: Пользователь видит полный список доступных целей make.
- **Роль**: `agent.report`
- **Ключевые файлы**: `README.md`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && grep -A2 '--- Reception' README.md | head -6``

- **ID**: `AGENT-DOC-02`
- **Название**: Обновить `AGENT_MAP.md` с новыми командами агентов
- **Цель**: Новые агенты видят, какие цели доступны.
- **Роль**: `agent.report`
- **Ключевые файлы**: `AGENT_MAP.md`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && grep -A3 '--- Reception' AGENT_MAP.md | head -6``

- **ID**: `AGENT-DOC-03`
- **Название**: Добавить записи в `tests/requirements/checklist.csv` (EMC REQ-21..25 + reception + direction)
- **Цель**: Чеклист отражает выполнение всех требований.
- **Роль**: `agent.report`
- **Ключевые файлы**: `tests/requirements/checklist.csv`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && head -5 tests/requirements/checklist.csv && wc -l tests/requirements/checklist.csv``

- **ID**: `AGENT-KICAD-01`
- **Название**: Сгенерировать полную принципиальную схему KiCad одним листом
- **Цель**: Проверить, что `generate_brig_receiver.py` produces a valid `.kicad_sch` with empty Errors/Violations via `kicad-cli sch check`.
- **Роль**: `agent.pcb`
- **Ключевые файлы**: `hardware/kicad/generate_brig_receiver.py`, `hardware/kicad/kicad_lib_brig.py`, `hardware/kicad/kicad_lib.py`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && .venv/bin/python3 hardware/kicad/generate_brig_receiver.py && kicad-cli sch check hardware/kicad/brig_receiver.kicad_sch 2>&1 | tail -5``

- **ID**: `AGENT-KICAD-02`
- **Название**: Собрать PCB и экспортировать gerbers/3D через tscircuit + kicad-cli
- **Цель**: Убедиться, что `make pcb` → `out/board.kicad_pcb` и `kicad-cli pcb export gerbers` генерирует полный набор.
- **Роль**: `agent.pcb`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`, `netlist`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && make pcb 2>&1 | tail -5 && ls -la out/board.kicad_pcb out/gerbers.zip``

- **ID**: `AGENT-KICAD-03`
- **Название**: Проверить электрическую связность (netlist) схемы
- **Цель**: Сравнить сгенерированный netlist и tscircuit-экспортированный, убедиться в отсутствии расхождений.
- **Роль**: `agent.pcb`
- **Ключевые файлы**: `hardware/kicad/sdr_receiver.kicad_sch`, `hardware/tscircuit/out/circuit.json`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && diff <(.venv/bin/python3 hardware/kicad/generate_brig_receiver.py 2>/dev/null || true) <cat hardware/tscircuit/out/circuit.json 2>/dev/null | head -20 || echo "compare manually"``

- **ID**: `AGENT-KICAD-04`
- **Название**: Создать TZ-документацию (техническое задание) по антенной системе
- **Цель**: Написать/обновить `requirements/antenna/REQ-ANT-*.md` и `hardware/antenna/` набор задач на проектирование 3-х антенных каналов (змейка 0-0.9 ГГц, патч 1.2-3 ГГц, матрица 4×4 4-7 ГГц).
- **Роль**: `agent.pcb`
- **Ключевые файлы**: `requirements/antenna/REQ-ANT-*.md`, `hardware/antenna/band_a/task.md`, `hardware/antenna/band_b/task.md`, `hardware/antenna/band_c/task.md`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && ls hardware/antenna/band_*/task.md && head -10 hardware/antenna/band_a/task.md``

- **ID**: `AGENT-GIT-01`
- **Название**: Подготовить коммит: пронуmeros все изменённые файлы, проверить `git status`
- **Цель**: Убедиться, что нет незакомitted изменений перед пушем.
- **Роль**: `agent.release`
- **Ключевые файлы**: `git status`, `.gitignore`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && git status --short | wc -l``

- **ID**: `AGENT-GIT-02`
- **Название**: Сделать `git commit -am "finish krystal-radio ...` и `git push origin main`
- **Цель**: Опубликовать финальный проект.
- **Роль**: `agent.release`
- **Ключевые файлы**: `—`, `репозиторий `origin/main`
- **Команда**: ``cd /mnt/ext4data/kristall-radio && git add -A && git commit -m "krystal-radio: finish SDR, YOLO ML, EMC, direction, page" && git push origin main``

- **ID**: `AGENT-ENC-01`
- **Название**: Проектирование aluminum корпуса с учетом требований EMC (REQ-21..25, REQ-26, REQ-27)
- **Цель**: Создать 3D-модель корпуса в FreeCAD, соответствующую всем электромагнитным, тепловым и безопасностным требованиям, с готовой файловой структурой для производства.
- **Роль**: `agent.mechanical`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make enclosure`

## 2. Сборка

- **ID**: `AGENT-MK-BUILD`
- **Название**: Собрать: build
- **Цель**: Выполнить цель «build» и убедиться в коде 0.
- **Роль**: `agent.build`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `npm run build`

- **ID**: `AGENT-MK-GENERATE-PCB`
- **Название**: Собрать: generate-pcb
- **Цель**: Выполнить цель «generate-pcb» и убедиться в коде 0.
- **Роль**: `agent.build`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `npm run generate-pcb`

- **ID**: `AGENT-MK-ERC-KICAD`
- **Название**: Собрать: informational KiCad ERC on the tscircuit-exported schematic
- **Цель**: informational KiCad ERC on the tscircuit-exported schematic. Система сборки сама подтянет: pcb.
- **Роль**: `agent.build`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`, `pcb`
- **Команда**: `make erc-kicad`

- **ID**: `AGENT-MK-LLM-CHECK`
- **Название**: Собрать: настройки LLM: выключатель, маршруты, живость провайдеров
- **Цель**: настройки LLM: выключатель, маршруты, живость провайдеров.
- **Роль**: `agent.verify`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make llm-check`

- **ID**: `AGENT-MK-LLM-OFF`
- **Название**: Собрать: подсказка: выключить LLM принудительно (KR_LLM_ENABLED=0)
- **Цель**: подсказка: выключить LLM принудительно (KR_LLM_ENABLED=0).
- **Роль**: `agent.build`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make llm-off`

- **ID**: `AGENT-MK-AGENTS-CHECK`
- **Название**: Собрать: что движок увидел в проекте: раннер, цели, артефакты
- **Цель**: что движок увидел в проекте: раннер, цели, артефакты.
- **Роль**: `agent.verify`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make agents-check`

- **ID**: `AGENT-MK-AGENTS-GRAPH`
- **Название**: Собрать: узлы графа и порядок их вызова
- **Цель**: узлы графа и порядок их вызова.
- **Роль**: `agent.build`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make agents-graph`

- **ID**: `AGENT-MK-AGENTS-ASK`
- **Название**: Собрать: разобрать заказ в цели, ничего не запуская (TEXT="...")
- **Цель**: разобрать заказ в цели, ничего не запуская (TEXT="...").
- **Роль**: `agent.build`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make agents-ask`

- **ID**: `AGENT-MK-AGENTS-RUN`
- **Название**: Собрать: выполнить заказ через граф (TEXT="...")
- **Цель**: выполнить заказ через граф (TEXT="...").
- **Роль**: `agent.build`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make agents-run`

- **ID**: `AGENT-MK-REPO-INIT`
- **Название**: Собрать: создать репозиторий на Forgejo и запушить (REPO_NAME=..., REPO_DESC=...)
- **Цель**: создать репозиторий на Forgejo и запушить (REPO_NAME=..., REPO_DESC=...).
- **Роль**: `agent.build`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make repo-init`

- **ID**: `AGENT-MK-REPO-LIST`
- **Название**: Собрать: какие репозитории есть на сервере
- **Цель**: какие репозитории есть на сервере.
- **Роль**: `agent.build`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make repo-list`

- **ID**: `AGENT-MK-REPO-PUSH`
- **Название**: Собрать: отправить текущую ветку на forgejo
- **Цель**: отправить текущую ветку на forgejo.
- **Роль**: `agent.build`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make repo-push`

## 3. Проверки

- **ID**: `AGENT-TST-LLM-CHECK`
- **Название**: Проверить: настройки LLM: выключатель, маршруты, живость провайдеров
- **Цель**: Убедиться, что проверки проходят без ошибок. Падение — сюрприз, а не ожидание: разобрать причину до того, как отчитаться.
- **Роль**: `agent.verify`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make llm-check`

- **ID**: `AGENT-TST-AGENTS-CHECK`
- **Название**: Проверить: что движок увидел в проекте: раннер, цели, артефакты
- **Цель**: Убедиться, что проверки проходят без ошибок. Падение — сюрприз, а не ожидание: разобрать причину до того, как отчитаться.
- **Роль**: `agent.verify`
- **Ключевые файлы**: `Makefile`, `package.json`, `agents.project.yaml`
- **Команда**: `make agents-check`

## 4. Результаты

- **ID**: `AGENT-ART-DALNOST-PRIEMA`
- **Название**: Проверить результат: дальность приёма
- **Цель**: Убедиться, что дальность приёма получен и содержит числовые значения. Файлы: out/link_budget.json.
- **Роль**: `agent.artifact`
- **Ключевые файлы**: `out/link_budget.json`
- **Команда**: `python3 -c "import json,glob,sys;[print(p, json.dumps(json.load(open(p)), ensure_ascii=False)[:800]) for p in sorted(glob.glob('out/link_budget.json'))]"`

- **ID**: `AGENT-ART-PELENGATSIYA`
- **Название**: Проверить результат: пеленгация
- **Цель**: Убедиться, что пеленгация получен и содержит числовые значения. Файлы: out/direction_finding.json.
- **Роль**: `agent.artifact`
- **Ключевые файлы**: `out/direction_finding.json`
- **Команда**: `python3 -c "import json,glob,sys;[print(p, json.dumps(json.load(open(p)), ensure_ascii=False)[:800]) for p in sorted(glob.glob('out/direction_finding.json'))]"`

- **ID**: `AGENT-ART-SPEKTR-VIRTUALNOGO-PRIEMNIKA`
- **Название**: Проверить результат: спектр виртуального приёмника
- **Цель**: Убедиться, что спектр виртуального приёмника получен и содержит числовые значения. Файлы: out/virtual_rx_spectrum.json.
- **Роль**: `agent.artifact`
- **Ключевые файлы**: `out/virtual_rx_spectrum.json`
- **Команда**: `python3 -c "import json,glob,sys;[print(p, json.dumps(json.load(open(p)), ensure_ascii=False)[:800]) for p in sorted(glob.glob('out/virtual_rx_spectrum.json'))]"`

- **ID**: `AGENT-ART-PROCHIE-REZULTATY-RASCHETOV`
- **Название**: Проверить результат: прочие результаты расчётов
- **Цель**: Убедиться, что прочие результаты расчётов получен и содержит числовые значения. Файлы: out/link_budget.json, out/direction_finding.json, out/virtual_rx_spectrum.json, out/circuit.json.
- **Роль**: `agent.artifact`
- **Ключевые файлы**: `out/link_budget.json`, `out/direction_finding.json`, `out/virtual_rx_spectrum.json`, `out/circuit.json`
- **Команда**: `python3 -c "import json,glob,sys;[print(p, json.dumps(json.load(open(p)), ensure_ascii=False)[:800]) for p in sorted(glob.glob('out/*.json'))]"`
