# Аппаратный цикл мультиагентной системы

Документ описывает **шаг-за-шагом industrial-процесс** разработки изделий
(на примере фонарика / аккустики / СВЧ) средствами уже существующей
машинерии Krystal Radio.
Идея: **каждый агент пишет артефакты на диск, следующий читает их,
используя единый make/скрипты. Никакого жёсткого задания типа "проект 
lighting → xyz".**

## 1. Цикл (MAGE-style, кривое сокращён)

```
Порядок выполнения (восстановление kристал ради́о):
  1. Consultant   — читает эталонную структуру из technical_task.md
                    и уточняет то, чего в заказе нет (REQ-нумерация)
  2. Theorist    — считает физику/показатели из заказа
  3. Designer    — собственный формат/скрипт, как gen_skidl_circuit.py,
                    генерирует out/netlist, board.svg, drawings.skidl
  4. Analyst     — проверяет нетлист / ERC / то что тестики прошли
  5. Tester      — make test (ERC/DRC, ERC/DRC (ERC/DRC, thermal scan и т.д.)
  6. Optimizer   — возможно, добрысерия-параметры(optics/power/thermal)
  7. Report      — make page (воображаемом Web-страницу с ссылками на
                    артефакты, README-манифест и т.д.) 
```

## 2. Никакого «жёсткого машинерии»

- Никаких `if order_type == "lighting"`.
  Каждый агент делает **только своё**.
- Совой step **обозначение только через файл артефакт**:
  * Агент-ID : id, текст, target/цель/назначение, generated files listed
    в `agents.project.yaml` artifacts-section (mkvé).
- **Выполнение команд/скриптов и выходные пути** переносится всё
  через `make all` и `make test`, выступающую с `agents.project.yaml`
  (согласно make rules на основе шаблона `AGENT_MAP.md`).
- Агенты никогда не создают «фиктивные» данные, если реальных данных
  (высчитано термодинамических  расчёттер.льными данными TORP, geometry,
  material предлорядки, load methods.

## 3 . Shipping scripts (адаптация Krystal Radio)

<|constrain|> Скрипт (Прородитель) | Что делает | Для нашего агента |
|---|---|---|
| `hardware/kicad/generate_brig_receiver.py` | генерирует kicad-схему | адаптируем к flashlighht (только видимость листа, нет hardcoded naming). |
| `tools/build_page.py` | html страницу | уже воспроизводится в scripts/generate.py (`page`) |
| `sdr_skidl_circuit_sklib.py` | скрипты для SKiDL | адаптируем с параметрами ordering parameter |
| `kicad_lib_brig.py` | для KiCad-проекты | без hardcode