/**
 * Конфиг tscircuit для оффлайновой сборки.
 *
 * Зачем он в модуле, а не в tscircuit.config.json: туда попадает только
 * JSON, а здесь нужен живой обработчик загрузки корпусов — функция.
 *
 * Что не так без него. Штатный загрузчик корпусов в tscircuit ходит на
 * https://kicad-mod-cache.tscircuit.com за каждым корпусом поимённо. Это
 * обнаружено запуском сборки в сетевом пространстве без интерфейса:
 *
 *   external_footprint_load_error: 73
 *   failed to load external footprint "kicad:Connector/JWT_A3963_...":
 *   getaddrinfo ETIMEOUT kicad-mod-cache.tscircuit.com
 *
 * То есть все 73 корпуса платы раньше скачивались с сервера tscircuit, и
 * без сети плата собиралась «всё равно» только потому, что сеть была.
 *
 * Что здесь сделано. footprintLibraryMap.kicad переопределён на чтение
 * .kicad_mod из локального каталога KiCad через штатный конвертер
 * kicad-to-circuit-json. Формат результата тот же, что ожидает ядро:
 * { footprintCircuitJson, cadModel }.
 *
 * Путь к библиотекам не зашит: он берётся из переменной KICAD_FP_ROOT, а
 * если её нет — из стандартных местоположений KiCad. Список проверяется по
 * факту наличия файла, так что перенос на другую машину не требует правок.
 */

import { existsSync, readFileSync } from "node:fs"
import { join } from "node:path"

import { KicadFootprintToCircuitJsonConverter } from "kicad-to-circuit-json"

// Каталоги с .pretty, где tscircuit ищет корпуса. Первый существующий
// выигрывает; список покрывает и Debian/Ubuntu, и установку в /Applications.
const FP_ROOTS = [
  process.env.KICAD_FP_ROOT,
  "/usr/share/kicad/footprints",
  "/usr/local/share/kicad/footprints",
  join(process.env.HOME ?? "", ".local/share/kicad/footprints"),
].filter(Boolean) as string[]

function footprintFile(name: string): string | null {
  // name приходит как "Lib/Name" (префикс kicad: уже срезан ядром).
  const slash = name.indexOf("/")
  if (slash < 0) return null
  const lib = name.slice(0, slash)
  const fp = name.slice(slash + 1)
  for (const root of FP_ROOTS) {
    const p = join(root, `${lib}.pretty`, `${fp}.kicad_mod`)
    if (existsSync(p)) return p
  }
  return null
}

async function loadKicadFootprintLocally(footprintName: string) {
  const path = footprintFile(footprintName)
  if (!path) {
    throw new Error(
      `Корпус "${footprintName}" не найден локально. Искали в: ` +
        FP_ROOTS.join(", ") +
        `. Проверьте, что пакет kicad-footprints установлен, ` +
        `либо задайте KICAD_FP_ROOT.`,
    )
  }
  const conv = new KicadFootprintToCircuitJsonConverter()
  conv.addFile(path, readFileSync(path, "utf8"))
  conv.runUntilFinished()
  // Ту же фильтрацию делает штатный загрузчик: убирает текст "REF**",
  // иначе на плате появляется лишняя надпись-заглушка.
  const footprintCircuitJson = conv
    .getOutput()
    .filter((el: any) =>
      el?.type === "pcb_silkscreen_text" ? el?.text === "REF**" : true,
    )
  // Модель не подключаем: STL приходит через <cadmodel modelUrl> из
  // локально сконвертированных файлов, и адрес model_step_url здесь
  // оставил бы в circuit.json ссылку на сервер tscircuit.
  return { footprintCircuitJson }
}

// ── движок подбора деталей ──────────────────────────────────────────────────
//
// Штатный JlcPcbPartsEngine (@tscircuit/eval) ходит на свой сервис
// jlcsearch.tscircuit.com/{resistors,capacitors,...}/list — это сеть, а нам
// нужно оффлайн. Алгоритм при этом полезный и перенесён дословно: фильтр по
// категории и корпусу, затем приоритет базовых деталей (у штатного сортировка
// по is_basic) и первые три позиции.
//
// Отличие одно: источник данных локальный. Репозиторий tscircuit/jlcsearch
// пересобирает исходную базу jlcparts в производные таблицы с ЧИСЛОВЫМИ
// колонками (lib/db/derivedtables/resistor.ts: resistance, tolerance_fraction,
// power_watts, is_basic, is_preferred, price1), и по ним идёт индексированный
// запрос. Здесь те же таблицы собирает hardware/tscircuit/build_derived.py, а
// подбор делает hardware/tscircuit/resolve_parts.py — по тем же правилам и по
// тем же именам колонок.
//
// fetchPartCircuitJson не задаётся намеренно: он нужен только для корпусов
// вида jlcpcb:<номер>, а у нас все корпуса kicad:.

type Resolved = Record<string, { jlcpcb: string[] } | undefined>

let resolvedCache: Resolved | null = null

function resolvedParts(): Resolved {
  if (resolvedCache) return resolvedCache
  const p = join(
    __dirname,
    "hardware/tscircuit/out/resolved_parts.json",
  )
  resolvedCache = existsSync(p)
    ? (JSON.parse(readFileSync(p, "utf8")).parts as Resolved)
    : {}
  return resolvedCache
}

const partsEngine = {
  findPart({ sourceComponent, footprinterString }: {
    sourceComponent: { name?: string; manufacturer_part_number?: string }
    footprinterString?: string
  }) {
    const parts = resolvedParts()
    // Ключ — обозначение компонента: так его кладёт эмиттер, и это же
    // поле source_component.name. Если обозначения нет, подбор не
    // выполняется: угадывать деталь по одному номиналу без корпуса нельзя.
    const hit = sourceComponent?.name ? parts[sourceComponent.name] : undefined
    if (!hit) return {}
    return { jlcpcb: hit.jlcpcb }
  },
}

export const config = {
  platformConfig: {
    footprintLibraryMap: {
      kicad: loadKicadFootprintLocally,
    },
    partsEngine,
    // Автотрассировщик локальный (capacity-autorouter), облачный не трогаем.
    useCloudAutorouter: false,
  },
}

export default config
