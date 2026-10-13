# tests/requirements/reception/
Тесты дальности приёма (link budget) и пеленгации (direction finding).

## Что тестируется
- Энергетика тракта: шумовая дорожка 2.7 кГц / NF 3.5 дБ (REQ-04, REQ-37),
  MDS ≤ −110 дБм (REQ-37), Friis и максимальная дальность по каналам A/B/C.
- «Дальность приёма» по разделам требований — `requirements/reception_range.md`
  и столбец `Reception_Distance_m` в `requirements_matrix.csv`.
- Фазовый интерферометр: Δφ = 2π·d·sinθ/λ, θ = asin(λ·Δφ/(2π·d)),
  зона однозначности (d ≤ λ/2), СКО пеленга σ_θ = λ/(2π·d·cosθ)·σ_φ,
  усиление решётки, генерация `.nec` и wires-JSON для AntennaSim.

## Модули под тестом
- `sim/scripts/link_budget.py` — энергетика тракта и дальность (цель `make link-budget`)
- `sim/scripts/direction_finding.py` — пеленгация и диаграмма (цель `make direction`)

## Как запускать
.venv/bin/pytest tests/requirements/reception/ -v

## Источник
requirements/blocks/req_04.md, req_36.md, req_37.md, req_antenna_system.md;
AntennaSim (EA1FUO) / NEC-2.
