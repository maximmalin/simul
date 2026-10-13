# tests/requirements/
Автоматические тесты Brig Receiver по матрице требований.

## Порядок работы
1. Тесты запускаются через `.venv/bin/pytest tests/requirements/ -v`.
2. Чек-лист обновляется вручную при появлении новых требований или готовности тестов.
3. Расчёты (calculations/, math/) не используются — только параметры из требований и reference-файлов.

## Подпапки
- `tests/requirements/antenna/` — антенная система (REQ-A1/B1/C1/SW1/ANT-ALL)
- `tests/requirements/rf/` — RF Frontend (REQ-01/03/04/05/06)
- `tests/requirements/ao/` — акустооптический процессор (REQ-07/08/09/11/13/14/15)
- `tests/requirements/system/` — системные требования (REQ-01 покрытие, диапазон 0–7 ГГц)
- `tests/requirements/checklist.csv` — чек-лист требований
- `tests/requirements/README.md` — описание подпапок
- `tests/requirements/checklist/README.md` — описание чек-листа

## Источник
- requirements/blocks/req_*.md
- tests/README.md
