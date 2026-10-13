# tests/
Автоматические тесты Brig Receiver по матрице требований.

## Цель
Автоматические тесты (pytest) и чек-лист (checklist.csv) покрывают все требования из папки `requirements/blocks/req_*.md`, не затрагивая расчёты (calculations/, math/).

## Структура
- `tests/` — корень тестов
- `tests/requirements/` — подпапки по блокам требований
- `tests/requirements/antenna/` — антенная система (REQ-A1/B1/C1/SW1/ANT-ALL)
- `tests/requirements/rf/` — RF Frontend (REQ-01/03/04/05/06)
- `tests/requirements/ao/` — акустооптический процессор (REQ-07/08/09/11/13/14/15)
- `tests/requirements/system/` — системные требования (REQ-01 покрытие, диапазон 0–7 ГГц)
- `tests/requirements/checklist.csv` — чек-лист требований
- `tests/README.md` — общее описание
- `tests/requirements/README.md` — описание подпапок

## Запуск
.venv/bin/pytest tests/requirements/ -v

## Чек-лист
- `checklist.csv` — чек-лист требований с колонками: req_id, title, block, threshold, test_file, status, ref_file
- Статус: implemented / pending / n/a
- При изменении требований — обновляем тест и чек-лист
