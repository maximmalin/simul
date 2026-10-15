# vendor/gnuradio-world

Submodule: [777arc/gnuradio-world](https://github.com/777arc/gnuradio-world)
— GNU Radio целиком в браузере (WASM), редактор флоуграфов, ~40 out-of-tree
блоков, примеры, записи IQ. Живая версия: <https://gnuradioworld.com>.

Зачем он здесь: у нас уже есть нативная ко-симуляция
(`src/gnuradio_krystal/`, `make gnuradio` → водопад + спектр). Этот submodule
даёт **второй, браузерный** путь — посмотреть и потрогать флоуграфы GNU Radio
без установки, и заимствовать готовые блоки/примеры для проверки наших сценариев.

## Получить

```bash
git submodule update --init --depth 1 frontend/vendor/gnuradio-world
```

## Запустить редактор

У него **собственный** `package.json` и зависимости — корневой
`npm install` фронтенда его не покрывает:

```bash
cd frontend/vendor/gnuradio-world/editor
npm install
npm run blocks      # генерирует public/blocks.json из реестра (обязательно)
npm run dev         # Vite dev-сервер редактора
```

`npm run blocks` требует `python3` — он зовёт
`python3 ../runner/gen_registry.py` и `gen/gen_blocklib.py`.

## Нужны подмодули самого gnuradio-world

Клонированные без рекурсии, поэтому `gnuradio/`, `gr-foo/`, `gr-rds/` и
остальные OOT-каталоги пустые — это **git submodules внутри** submodule:

```bash
cd frontend/vendor/gnuradio-world
git submodule update --init --recursive
```

Полный набор тянет GNU Radio core (большой). Для просмотра примера и
редактирования флоуграфа хватает верхнего уровня; для сборки WASM-блоков
нужны все.

## Структура

| путь | что это |
|---|---|
| `editor/` | React + Vite редактор флоуграфов (основной фронтенд проекта) |
| `runner/` | WASM-раннер, выполняющий флоуграф |
| `workers/` | воркеры аудио/спектроанализа |
| `blocks/` | каталог блоков и их метаданные |
| `example_flowgraphs/` | ~40 готовых `.grc` примеров (ADS-B, AIS, LTE, radar…) |
| `gr-*` | out-of-tree модули (submodules) |
| `test/` | smoke-тесты на puppeteer |
| `AGENTS.md` | инструкции для ИИ-агентов по работе с репозиторием |

## Лицензия

GPL-3.0-or-later (см. `LICENSE` внутри submodule). Мы её **не** вендорим в
собственный код — только подключаем как внешний репозиторий.

## Интеграция с агентным мостом

Мост (`backend/agent_bridge.py`) отдаёт артефакты из `out/`. Если появится
браузерный раннер, его вывод логично складывать рядом:

```
out/
  waterfall.png            ← нативный GNU Radio (make gnuradio)
  virtual_rx_spectrum.json ← спектр
  krystal-radio.html       ← отчёт-страница
```