# Требования к приёмнику Krystal Radio

Изделие: **zero-IF SDR, прямое преобразование**, диапазон 0–7 ГГц.

```
J_ANT (SMA, 50 Ом) → Q_LNA1/Q_LNA2 (2N7002) + X_QWC1 (LPJ-1)
   → U_MIX1 (LT5560, zero-IF) → U_OP1 (OPA2134)
   → АЦП U1 (STM32F103C8T6, PA0/PA1) → USB CDC (J_USB) → ПК / GNU Radio
```

Требования сгруппированы **по подсистемам приёмника**, а не по
наследованной от отменённой акустооптической схемы структуре.
Источник истины по аппаратной части — `sim/scripts/sdr_skidl_circuit.py`
и `out/circuit.json`; структурная схема — `references/structural_block.drawio`.

## Подсистемы

| каталог | подсистема | требования |
|---|---|---|
| `rf/` | ВЧ-тракт: вход, усилители, фильтр, смеситель | REQ-RF-01…04 |
| `baseband/` | Буферы ПЧ и оцифровка | REQ-BB-01…02 |
| `antenna/` | Антенная панель, три канала | REQ-ANT-01…03 |
| `host/` | USB и GNU Radio | REQ-IO-01…02 |
| `power/` | Питание и развязка | REQ-PWR-01…02 |
| `mechanics/` | Корпус, экранирование, зонирование | REQ-MECH-01…02 |
| `emc/` | Электромагнитная совместимость | REQ-EMC-01…04 |
| `environment/` | Климат, вибрация, консервация | REQ-ENV-01…02 |
| `reliability/` | Надёжность | REQ-REL-01 |
| `safety/` | Электробезопасность | REQ-SAFE-01 |
| `performance/` | Полоса, чувствительность, SFDR | REQ-PERF-01…03 |
| `analysis/` | Расчётные характеристики | REQ-ANA-01…03 |

Всего **29 требований** в 12 подсистемах.

## Сводная таблица

| ID | Требование | Норма | Проверка |
|---|---|---|---|
| REQ-RF-01 | Вход SMA 50 Ом, 0–7 ГГц | ГОСТ 20275-91 | `tests/requirements/rf/` |
| REQ-RF-02 | NF ≤ 3.5 дБ, усиление ≥ 15 дБ | IEEE 145 | `tests/requirements/rf/` |
| REQ-RF-03 | Полосовой фильтр, разброс ≥ 40 дБ | — | ngspice `.ac` |
| REQ-RF-04 | Смеситель zero-IF, NF ≤ 8 дБ | — | `tests/requirements/rf/` |
| REQ-BB-01 | Дифференциальные буферы I/Q, полоса ПЧ 2.7 кГц | — | `verify_pin_contract.py` |
| REQ-BB-02 | АЦП 12 бит, ≤ 1 MSPS, контракт PA0/PA1/PA6/PA7 | DS5319 | `verify_pin_contract.py` |
| REQ-ANT-01 | Канал A: 0–900 МГц, КНД ≥ 5 дБи | — | `tests/requirements/antenna/` |
| REQ-ANT-02 | Канал Б: 1.2–3.0 ГГц, КНД ≥ 8 дБи | — | `tests/requirements/antenna/` |
| REQ-ANT-03 | Канал В: 4.0–7.0 ГГц, КНД ≥ 12 дБи | — | `tests/requirements/antenna/` |
| REQ-IO-01 | USB CDC, 22 Ом терминация | USB 2.0 | `make gnuradio` |
| REQ-IO-02 | Модуль GNU Radio, headless ко-симуляция | GNU Radio 3.10 | `tests/requirements/gnuradio/` |
| REQ-PWR-01 | Линейное питание 5→3.3 В, развязка | — | `sim/scripts/thermal_pcb.py` |
| REQ-PWR-02 | Феррит на аналоговой шине, 600 Ом@100 МГц | — | `tests/requirements/emc/` |
| REQ-MECH-01 | Корпус Al 0.5 мм, прокладки ≤ 10 мОм | MIL-DTL-83528 | `AGENT_ENCLOSURE.md` |
| REQ-MECH-02 | Три секции, «земля» звездой | — | `tests/requirements/emc/` |
| REQ-EMC-01 | Экранирование, δ(Al,1 ГГц) = 2.59 мкм | CISPR 22, MIL-DTL-83528 | `tests/requirements/emc/` |
| REQ-EMC-02 | Иммунитет 3 В/м, 80–2700 МГц | ГОСТ 30804.4.3-2013 | `tests/requirements/emc/` |
| REQ-EMC-03 | Эмиссия Класс Б | ГОСТ CISPR 22-2012 | `tests/requirements/emc/` |
| REQ-EMC-04 | Фильтры питания, f_c = 159 кГц | IPC-2221 | `tests/requirements/emc/` |
| REQ-ENV-01 | +15…+30 °C рабочий, −40…+50 °C хранение | ГОСТ 15150-69 | `make thermal` |
| REQ-ENV-02 | RH ≤ 80 %, вибрация, консервация ВЗ-10 | ГОСТ 9.014-78, MIL-STD-810G | `tests/requirements/` |
| REQ-REL-01 | MTBF ≥ 10 000 ч | — | `tests/requirements/reliability/` |
| REQ-SAFE-01 | Класс II, утечка ≤ 0.5 мА | IEC 60950-1 | `tests/requirements/emc/` |
| REQ-PERF-01 | Полоса, 50 Ом | — | `tests/requirements/rf/` |
| REQ-PERF-02 | NF, шумовая дорожка, MDS | IEEE 145 | `tests/requirements/reception/` |
| REQ-PERF-03 | SFDR ≥ 75–80 дБ | — | `tests/requirements/rf/` |
| REQ-ANA-01 | Дальность приёма A/B/C по Фриису | — | `sim/scripts/link_budget.py` |
| REQ-ANA-02 | Пеленг, зона однозначности, NEC-2 | — | `sim/scripts/direction_finding.py` |
| REQ-ANA-03 | Распознавание модуляции YOLO | — | `ml/`, `tests/requirements/ml/` |

## Известные ограничения первой ревизии

Эти ограничения зафиксированы как требования со статусом
«не выполнено», а не скрыты:

| ID | Ограничение | Следствие |
|---|---|---|
| REQ-BB-02 | АЦП STM32F103 ≤ 1 MSPS | нет потока 500 МГц |
| REQ-IO-01 | USB CDC Full-Speed ≈ 64 KSPS/канал | передача в реальном времени только буферизованная |
| REQ-PERF-01 | Фактическая полоса ≈ 2.7 кГц | требование 500 МГц не выполнено |

Пути развития описаны в соответствующих файлах требований.

## Исправленные ошибки исходной редакции

| параметр | было | стало | где |
|---|---|---|---|
| Глубина скин-слоя Al @1 ГГц | 0.025 мм | **2.59 мкм** | REQ-EMC-01 |
| Мощность наведённых помех | 1.8·10⁻⁵ Вт | **4.5·10⁻⁴ Вт** | REQ-EMC-02 |
| Частота среза LC-фильтра | 16 кГц | **159 кГц** | REQ-EMC-04 |
| Частота дискретизации АЦП | 2–4 MSPS | **≤ 1 MSPS** | REQ-BB-02 |
| Частота выборки строк | 50 кГц, 2048 пикс. | I/Q 12 бит ≤ 1 MSPS | REQ-IO-02 |

Тесты в `tests/requirements/emc/` падают при возврате любой из
первых трёх старых цифр.

## Вспомогательные файлы

- `requirements_matrix.csv` — сводная матрица с колонками раздела,
  приоритета, статуса и `Reception_Distance_m`
- `reception_range.md` — дальность приёма по разделам ТЗ