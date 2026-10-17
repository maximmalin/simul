# Библиотека первоисточников (электроника)

Откуда дизайнер берёт топологии узлов и правила расчёта. Иерархия
доверия:

1. **Даташит выбранной детали** — главный источник: типовая схема
   включения и расчёт из разделов Application Information / Typical
   Application / Design Requirements.
2. **Аппноты и справочники производителя** — проверенные узлы и
   формулы, привязанные к реальным сериям деталей.
3. **Книги** — принципы, методы расчёта, типовые построения.
4. Интернет-заметки и форумы — только наводка; без первоисточника это
   не основание.

Правило: каждая топология и каждое число — со ссылкой на конкретное
место (даташит: раздел/страница; аппнота: номер; книга: глава).

## 1. Справочники и аппноты производителей (бесплатные и легальные)

- TI **Analog Engineer's Pocket Reference** и **Analog Engineer's
  Circuit Cookbook** (Amplifiers; Data Converters) — готовые типовые
  включения ОУ/АЦП с формулами и проверенными номиналами.
- TI **Precision Labs** — систематический курс по ОУ, АЦП, источникам
  питания, помехам (видео и слайды).
- TI **Reference Designs** (TIDA-/TIDU-/TIDEP-) — законченные узлы с
  документацией, схемами и измерениями; ищи по функции.
- ADI **Linear Circuit Design Handbook** (Zumbahlen, главы в PDF) —
  справочник по всему аналоговому тракту.
- ADI **Op Amp Applications Handbook** (Jung) — ОУ: схемы, шумы,
  инструментальные усилители.
- ADI **Circuits from the Lab** (CN-xxxx) — проверенные схемы узлов.
- TI **Op Amps for Everyone** (Mancini/Carter); TI **Handbook of
  Operational Amplifier Applications** (SBOA092) — расчёт ОУ-узлов.
- **SMPS**: TI Power Topologies Handbook; аппноты TI/ADI по buck/boost
  и LDO (расчёт L/C, компенсация, тепловой режим, разводка).
- **Интерфейсы и защита**: RS-485 (TI SLLA070 и аналоги), CAN, I²C
  (NXP AN10216), ESD/TVS (TI, Littelfuse), опторазвязка (Vishay/
  Toshiba), ключи и реле (Infineon, TI).
- **Микроконтроллеры**: аппноты по обвязке (питание, сброс, тактовые,
  watchdog, АЦП) — у производителя конкретного МК.

## 2. Книги (когда есть в библиотеке или куплены)

- Horowitz & Hill, «The Art of Electronics», 3-е изд. (+ X-Chapters) —
  главный практический справочник разработчика.
- T. Williams, «The Circuit Designer's Companion» — практика: помехи,
  питание, монтаж, допуски.
- J. Williams, «The Art and Science of Analog Circuit Design»;
  Dobkin & Williams, «Analog Circuit Design» — разбор реальных узлов
  с анализом ошибок.
- B. Pease, «Troubleshooting Analog Circuits» — отладка и поиск причин.
- Pressman, «Switching Power Supply Design»; Erickson & Maksimovic,
  «Fundamentals of Power Electronics» — импульсные преобразователи.
- Ott, «Electromagnetic Compatibility Engineering» — ЭМС, фильтры,
  заземление.
- Johnson & Graham, «High-Speed Digital Design» — цифровые шины,
  целостность сигнала, согласование.
- Sedra & Smith, «Microelectronic Circuits» — базовые методы анализа;
  Razavi, «Design of Analog CMOS ICs» — внутренний уровень.
- T. Kuphaldt, «Lessons In Electric Circuits» (свободная) — основы.
- Graf, «Encyclopedia of Electronic Circuits» — картотека узлов
  (обязательно проверять современность решений).

## 3. Чем решение проверяется в проекте

- Симуляция: SPICE-стадия заказа (`make spice`), схемы узлов.
- Сборка и нетлисты: `python3 tools/skidl_blocks.py`.
- Символы/пины/корпуса: инструмент `kicad_libs`; паспорта и даташиты
  деталей — библиотекарь и упаковщик.
- Приёмка: чек-лист (`design/checklist.md`) — числа и критерии.
