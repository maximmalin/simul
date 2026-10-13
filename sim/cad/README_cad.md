# CAD ячейки Брэгга — просмотр и правка в FreeCAD

Папка `sim/cad/` содержит 3D-модель ячейки (два блока: стекло + кварц),
которую можно открывать и редактировать в **FreeCAD**.

## Файлы

| Файл | Назначение |
|---|---|
| `bragg_cell.FCStd` | **параметрический** документ FreeCAD: два `Part::Box` (`Glass`, `Quartz`). Размеры меняются в дереве — модель пересчитается |
| `bragg_cell.step`  | STEP-копия той же модели (для любого CAD/обмена) |
| `refresh_cad.py`   | перегенерация CAD из `../inputs/bragg_inputs.yaml` (см. ниже) |
| `make_bragg_cell_fc.py` | макрос FreeCAD, который строит модель (можно запускать в GUI: «Макросы → Выполнить макрос») |
| `cad_dims.json`    | текущие размеры в мм (промежуточный файл) |

Единицы — **миллиметры** (для удобства); физический размер соответствует
параметрам FEM (в yaml они в метрах).

## Как открыть

```
freecad sim/cad/bragg_cell.FCStd          # параметрическая модель
# или
freecad sim/cad/bragg_cell.step           # обычная импорт-модель
```

В дереве FreeCAD: `Glass` (синий) — стекло, `Quartz` (песочный) — кварц.
Правьте `Length/Width/Height` и `Placement` объекта — это и есть размеры
ячейки (`lx = 15 мм`, `ly = 5 мм`, `glass_lz = 2.91 мм`, `quartz_lz = 0.09 мм`).

## Перегенерация из входных параметров

После изменения `sim/inputs/bragg_inputs.yaml` обновите CAD:

```bash
cd /home/mirrage/Desktop/brig-receiver
.venv/bin/python3 sim/cad/refresh_cad.py
```

Скрипт возьмёт `geometry` из yaml, пересоздаст `bragg_cell.FCStd` и
`bragg_cell.step`.

## Как показывать решение FEM в CAD

Поля результатов (перемещения, напряжения) визуализируются в ParaView:
`sim/work/bragg_cell/bragg_results_t0001.vtu`. Сама CAD-модель — только
геометрия; для проверки размеров/зазоров она не нужна в симуляции.