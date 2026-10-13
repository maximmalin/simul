система/системные требования/Чек-лист Brig Receiver

Папки:
- tests/requirements/antenna/ — антенная система (REQ-A1/B1/C1/SW1/ANT-ALL)
- tests/requirements/rf/ — RF Frontend (REQ-01/03/04/05/06)
- tests/requirements/ao/ — акустооптический процессор (REQ-07/08/09/11/13/14/15)
- tests/requirements/system/ — системные требования (REQ-01 покрытие, диапазон 0–7 ГГц)
- tests/requirements/checklist/ — чек-лист требований (checklist.csv)
- tests/requirements/checklist.csv — чек-лист

Каждая подпапка содержит:
- test_*.py — тесты на pytest
- README.md — описание тестов

Чек-лист:
- checklist.csv — таблица требований с колонками: req_id, title, block, threshold, test_file, status, ref_file
- Статус: implemented / pending / n/a
- При изменении требований — обновляем тест и чек-лист
