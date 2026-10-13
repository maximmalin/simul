# tests/requirements/system/
Тесты системных требований по матрице требований (REQ-01 покрытие + диапазон 0–7 ГГц).

## Что тестируется
- REQ-01: мгновенная полоса частот ≥ 500 МГц (целевая 1000 МГц)
- REQ-01 + антенная система: полное покрытие 0–7000 МГц без пропусков
- REQ-01: покрытие ≥ 25× шире HackRF (20 МГц)

## Как запускать
.venv/bin/pytest tests/requirements/system/ -v

## Источник
requirements/blocks/req_01.md, req_antenna_system.md (REQ-ANT-ALL)
