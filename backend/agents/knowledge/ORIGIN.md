# Происхождение материалов

Методика, чек-листы и база знаний перенесены из
**https://github.com/Arcadia-1/analog-agents** (лицензия MIT).

Что перенесено и как адаптировано:

| Источник | У нас | Адаптация |
|----------|-------|-----------|
| `prompts/architect-prompt.md` | `knowledge/architect.md` | Spectre → ngspice, spec.yml → ТЗ на узел |
| `prompts/designer-prompt.md` | `knowledge/analog_design.md` | netlist → наша спецификация |
| `prompts/librarian-prompt.md` | `knowledge/librarian.md` | обход библиотек → `kicad_libs`/`part_search` |
| `prompts/verifier-prompt.md` | `knowledge/verifier.md` | margin-report → measurements.json |
| `checklists/*.yml` | `checklists/*.yml` | без изменений, кроме шапки |
| `wiki/**` | `knowledge/analog_wiki/**` | без изменений |
| `shared-references/*` | `knowledge/*` | без изменений, кроме имён инструментов |
| `skills/*/SKILL.md` | `skills/*/SKILL.md` | без Virtuoso/Spectre |

Что не перенесено и почему: `analog-netlist-crawl` (скрипты обхода
нетлистов Cadence), `virtuoso-librarian` и подмодули `ADCToolbox`,
`virtuoso-bridge-lite` (связаны с Cadence Virtuoso — у нас KiCad).

Права: MIT, источник указан выше.
