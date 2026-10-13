# Тесты страницы `out/krystal-radio.html`

Цель `make page` собирает **один самодостаточный HTML-файл**, в котором
одновременно есть:

* **3D-модель платы** — `board.glb` (экспорт tscircuit), встроена в base64
  и показывается интерактивным просмотрщиком three.js. Бандл просмотрщика
  (`hardware/tscircuit/viewer/main.js` → `dist/krystal-viewer.js`) собран
  rollup'ом из локального `node_modules/three`; CDN не используется.
* **технические чертежи** — SVG-чертежи ГОСТ (из `gost_pipeline.py`,
  панели антенн A/B/C) и чертёж платы из `kicad-cli`, встроенные как
  `data:image/svg+xml`-URI и переключаемые по вкладкам.

Страница открывается офлайн из `out/` и не тянет ничего из сети.

## Что проверяется

| Тест | Проверка |
| --- | --- |
| `test_page_exists` | артефакт собран |
| `test_page_is_single_file` | нет внешних `src`/`<link>`/`@import` |
| `test_page_has_3d_viewer_mount` | присутствует модуль и `KrystalViewer.mount` |
| `test_page_embeds_glb_model` | встроенный GLB валиден (magic `glTF`, v2, длина) |
| `test_glb_matches_published_model` | встроенная модель = `out/board.glb` |
| `test_page_has_technical_drawings` | встроены SVG-чертежи |
| `test_page_has_tabs_for_drawings` | число вкладок = числу чертежей |
| `test_expected_drawings_present` | есть чертёж платы и чертёж антенны A |
| `test_render_*` | headless Chrome создаёт WebGL-canvas и разбирает GLB |

Тесты, требующие Chrome (`test_render_*`), пропускаются, если браузер не
найден; если страница не собрана, весь модуль пропускается.

```bash
make page           # собрать out/krystal-radio.html
pytest tests/requirements/report -v
```
