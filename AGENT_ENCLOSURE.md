# Агент проектирования корпуса (Enclosure Design Agent)

## ID: `AGENT-ENC-01`
## Название: Проектирование aluminum корпуса с учетом требований EMC (REQ-21..25, REQ-26, REQ-27)
## Цель: Создать 3D-модель корпуса в FreeCAD, соответствующую всем электромагнитным, тепловым и безопасностным требованиям, с готовой файловой структурой для производства.

## Ключевые файлы
- `requirements/blocks/req_21.md` — Экранирование корпуса (SE ≥ 40 дБ, skin depth δ ≈ 2.6 мкм@1 ГГц)
- `requirements/blocks/req_22.md` — Иммунитет к внешним полям (3 В/м, IEC 61000-4-3 ур.2, SE 40–60 дБ, прокладки ≤10 мОм)
- `requirements/blocks/req_23.md` — Уровень радиопомех (CISPR 22 Class B, Wi-Fi ESP30, стабили LM7812/7805, keram. конденсаторы 0.1 мкФ)
- `requirements/blocks/req_23.md` — Энергопередача (линейные стабилизаторы вместо импульсных)
- `requirements/blocks/req_24.md` — Фильтры питания (ferrite beads R≈100 Ом@100 МГц, ker. конденсаторы на землю)
- `requirements/blocks/req_25.md` — Зонирование экранирования (3 зоны: ВЧ/оптика/цифра; галиваническая изоляция)
- `requirements/blocks/req_26.md` — Алюминиевый корпус: MTBF ≈ 50 000 ч (нет коррозии)
- `requirements/blocks/req_27.md` — Изоляция (3000 В между сетью 230В и низковольтными секциями)
- `requirements/blocks/req_29.md` — Заземление (screw ГОСТ 14192-96, утечка тока ≤ 0.5 mA)
- `requirements/blocks/req_30.md` — Консервация (силикагель ГОСТ 3956-76 тип V, 5–10 г при V ≈ 500 см³, срок 3 года)
- `requirements/blocks/req_block_06_10.md` — Корпус должен предотвращать проникновение наводок Wi-Fi ESP32 в приемный тракт

##Константы и материалы
- **Material**: Al Mg3 (алюминий марки Mg3) или Al 6061-T6 — легкий, buona machinability, good EMI shielding (σ ≈ 3.5×10⁷ См⁻¹).
- **Skin depth at 1 ГГц**: δ = √(2ρ/ωμ) ≈ 2.6 мкм для Al (фактическое требуется ≥ 3δ покрытия).
- **Min. thickness**: 0.5 мм (гарантирует 3δ при 1 ГГц, mechaniquement robust).
- **RFI gaskets**: Conductive elastomer, contact resistance ≤ 10 мОм (REQ-22).
- **Grounding screw**: М3, ГОСТ 14192-96, расположение на rear panel, torque 0.5 Н·м.
- **Silica gel**: ГОСТ 3956-76, тип V, 7±2 г при V_корпуса ≈ 500 см³, срок службы 3 года.
- **Condensation threshold**: RH > 80% при +25° C → требуется silica gel; иначе конденсат на кристалле TeO₂ и фотодиодах исказ Δn.

## Задачи агента

### 1. Анализ требований и определение спецификации корпуса
- Проверить все req_21..req_29 и req_block_06_10.
- Сформулировать спецификацию:
  - Материал: Al 6061-T6, min 0.5 мм толщина.
  - Гайки/прокладки: RFI-эластомер, R_contact ≤ 10 мОм.
  - Замковые отверстия: M3, 2 шт. на rear panel.
  - Заземляющий контакт: screw ГОСТ 14192-96, за Koregen plane.
  - Orifice для silica gel: маленькая надверхувязка (Ø 3–5 мм) с фильтром от пыли.
  - 3 зоны экранирования: (1) ВЧ-корпус (весь случай), (2).optical zone — удаление металла от чувствительных optical components, (3) цифровая zone — заземление плоскостей GND.
  - Insulation barrier: 3000 В между питанием и низковольтными секциями (CREEP/CLR per IEC 62368-1).

### 2. Создание 3D-модели в FreeCAD
- **Базовый корпус**: прямоугольный параллелепипед 120×80×30 мм (примерно, подобрать под PCB + радиаторы).
- **Отверстия для монтажа**: 4×M3 на 40×60 мм диагональ, 2×M3 на rear panel для заземления.
- **Отверстия для silica gel**: Ø 3 мм, 1 шт., с металлическим фильтром (laser-cut mesh 0.1 mm).
- **RFI-прокладки**: вы recesses (0.2–0.5 mm depth) по периметру крышки, для размещения conductive gasket.
- **Разъемы и интерфейсы**: SMA разъемы (антенны), USB-C или barrel jack для питания, GPIO/header для программирования ESP32.
- **Отверстия для вентиляции (если необходимо)**: минимальные, чтобы не ухудшать SE < 20 дБ в вышетRF диапазонах.

### 3. Генерация производственной документации
- **STEP file**: `hardware/enclosure/krystal-case.step` (только корпус, без внутренних деталей).
- **STL file**: для 3D-печати проверки форм (PLA/PETG).
- **FreeCAD文件**: `hardware/enclosure/krystal-case.FCStd` (все параметрические ограничения).
- **Nc-path (опционально)**: G-код для CNC-fräsen из Al листа (0.5–1 мм).
- **BOM-item**: "Корпус Krystal Radio Al 6061-T6, 0.5 мм, с RFI-прокладками иsilica gelチャamber".

### 4. Валидация по требованиям
- Вычислить SE ( shielding effectiveness) для Al 0.5 мм: SE ≈ 20·log₁₀( (η₀/(2·σ·δ)) + 1 ) ≈ 45–50 дБ при 1 ГГц (соответствует REQ-21).
- Проверить горячий пробник: T_j = T_a + P·θ_JA — убедиться, что при worst-case Power dissipation ΔT < 54 K (чтобы T_j ≤ 85°C при Ta=31°C).
- Проверить заземление: R_contact ≤ 10 мОм → I_leakage = V_isolation / R ≥ 500V / 0.01Ω = 50 A (теоретическая максимум, на практике токи микроамперские за счет скошенных контактов).
- Симуляция конденсации: RH > 80% → silica gel qty = 7 г, срок 3 года.

### 5. تسليم и проверка
- Сделать коммит: `git add hardware/enclosure/ && git commit -m "enclosure: design per REQ-21..25, REQ-26, REQ-27"`
- Сделать `git push origin main`
- Обновить `AGENT_ENCLOSURE.md` статусом "completed".

## Пример FreeCAD Python-скрипта (базовый)

```python
import FreeCAD as FC
import Part as PT
import Draft as DT

# --- Параметры ---
mat_th = 0.5        # толщина стенок (mm)
ext_len, ext_wid, ext_h = 120, 80, 30  # внешние размеры (mm)
int_len, int_wid, int_h = ext_len - 2*mat_th, ext_wid - 2*mat_th, ext_h - mat_th  # внутренние

# --- Создание корпуса ---
# Внешний короб
outer = FC.Workplane("XY").box(ext_len, ext_wid, ext_h)
# Внутренний полый prostor
inner = FC.Workplane("XY").box(int_len, int_wid, int_h)
# Вычитание: корпус с полостью
body = outer.cut(inner)

# Сохранение
FC.document = FC.newDocument("Enclosure")
FC.addObject("Part::Feature", "Case").Shape = body
FC.document.saveAs("hardware/enclosure/krystal-case.FCStd")
```

## Чек-лист готовности (Completion Checklist)
- [ ] Спецификация корпуса задокументирована в `requirements/blocks/req_21..29` заполнена.
- [ ] 3D-модель FreeCAD сохранена как `.FCStd` и `.step`.
- [ ] R_{contact} ≤ 10 мОм для RFI-прокладок рассчитано/измерено.
- [ ] Orifice для silica gel добавлен (Ø 3 мм + сетка).
- [ ] Замковые отверстия M3 добавлены для заземления.
- [ ] Симуляция SE ≥ 40 дБ при 1 ГГц пройдена (Al 0.5 мм).
- [ ] T_j max расчет показывает ΔT < 54 K (при Ta=31°C).
- [ ] BOM-item and документация обновлены.
- [ ] git commit + push выполнен.
