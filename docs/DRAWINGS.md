# Конструкторские чертежи (engineering drawings)

Модуль `backend/drawing_sheet.py` строит чертёж детали в виде PDF + SVG:
проекции из геометрии (STEP), измеренные размеры, выноски отверстий,
скрытые линии, центровые, основная надпись по ГОСТ 2.104, таблица
изменений, технические требования. Реализация своя (рендерер matplotlib,
проекции FreeCAD), но идеи портированы из text-to-cad `eng_drawing` и
улучшены под ГОСТ.

## Быстрый старт

```bash
make drawings            # band_a/b/c + корпус → sim/cad/figures/*_GOST.pdf/.svg
make drawings-legacy     # старый пайплайн (FreeCAD TechDraw, DXF)
```

## API

```python
from backend.drawing_sheet import make_sheet_from_step, render

sheet = make_sheet_from_step(
    "hardware/antenna/geometry/band_a.step",
    title="Антенна A", part_number="ANT-A", material="FR4",
    general_tolerance="ISO 2768-m",
    notes=["Размеры в мм."],
    revisions=[("A", "2026-11-10", "ПЕРВЫЙ ВЫПУСК")],
    size="A2", iso=True,
)
# ручные размеры
view = sheet.views[0]  # top
view.dim((0, 0, 0), (150, 0, 0), tol=0.1)
view.hole((75, 75, 0), 6.6, depth=12, count=4)
view.note("Фаска 1×45°", (10, 10, 0), offset=(12, 8))
render(sheet, "out/drawing.pdf", "out/drawing.svg")
```

## Декларативная спецификация

Рядом со STEP можно положить `<name>.dims.json`:

```json
{
  "title": "Антенна A",
  "part_number": "ANT-A",
  "material": "FR4",
  "general_tolerance": "ISO 2768-m",
  "notes": ["Размеры в мм."],
  "dims": [{"p1": [0,0,0], "p2": [150,0,0], "orientation": "h", "tol": 0.1}],
  "holes": [{"center": [75,75,0], "diameter": 6.6, "depth": 12, "count": 4}]
}
```

`make_sheet_from_step` подхватит его автоматически (или передайте `spec={...}`).

## Самопроверки

При `render()` печатаются предупреждения:
- «dimension point … lands outside view geometry (measures blank paper)» — размер мерит пустую бумагу.
- «annotation overlaps: …» — наложение аннотаций.
- «view … runs off the frame» — выход за рамку (с предложением масштаба).

## Детерминизм

PDF не содержит дату создания → повторная сборка даёт байт-в-байт идентичный файл.
