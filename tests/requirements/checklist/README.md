# tests/requirements/checklist/
Чек-лист требований Brig Receiver.

## Файлы
- `checklist.csv` — чек-лист требований с колонками: req_id, title, block, threshold, test_file, status, ref_file
- `README.md` — описание чек-листа

## Связь с тестами
- Каждая строка чек-листа имеет поле `test_file` — путь к тесту
- Статус `status` обновляется вручную при готовности теста

## Пример
```
req_id,title,block,threshold,test_file,status,ref_file
REQ-A1,Антенна Канал А (0–900 МГц),Антенна,диапазон 0–900 МГц, SWR≤2.0, импеданс 50±10 Ом, КНД≥5 дБи на 433 МГц,тесты/requirements/antenna/test_antenna.py#test_req_a1_*,requirements/blocks/req_antenna_system.md#REQ-A1
REQ-01,Мгновенная полоса частот,RF,БПЧ ≥500 МГц (целевая 1000 МГц),тесты/requirements/rf/test_rf.py#test_req_01_*,requirements/blocks/req_01.md
```

## Обновление
При изменении требований или появлении новых тестов — обновляем чек-лист.
