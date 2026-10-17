#!/usr/bin/env node
/**
 * krystal-radio (zero-IF SDR) -> код tscircuit из нетлиста KiCad, затем экспорт.
 *
 * Разделение ответственности:
 *   ЭТОТ ФАЙЛ делает ровно одно: превращает проверенный нетлист KiCad
 *   в файл board.circuit.tsx. Рисовать электрику вручную он не должен —
 *   он лишь переносит то, что уже подтверждено kicad-cli.
 *
 *   ВСЁ ОСТАЛЬНОЕ делает официальный tscircuit CLI (tsci):
 *     корпуса           -> kicad:<Lib>/<Name>, резолвятся локально
 *     автотрассировка   -> @tscircuit/capacity-autorouter (встроен в core)
 *     экспорт платы     -> -f kicad_pcb / kicad_zip / gerbers
 *     экспорт 3D        -> -f step / glb
 *     нетлист/проект    -> -f readable-netlist / kicad_sch
 *
 * Почему нет готового конвертера: ни один официальный пакет не умеет
 * KiCad-схему -> автотрассируемая плата. circuit-json-to-tscircuit
 * проверен и выдаёт нерабочий код (имя компонента "undefined", ноль
 * <trace>), поэтому код генерируется здесь, а обрабатывает его tsci.
 *
 * Адресация выводов: REF.pin<N>, где N — номер вывода из нетлиста KiCad.
 * Это канонический селектор tscircuit. Все компоненты эмитятся как <chip>
 * с явными pinLabels, поэтому полярность диодов, светодиодов и
 * электролитов исключена по построению: KiCad вывод N -> pinLabels[N] ->
 * REF.pinN, никакой догадки о том, где anode, не требуется.
 *
 * Запуск:  node hardware/tscircuit/build_pcb.mjs
 */

import { readFileSync, mkdirSync, writeFileSync, existsSync } from "node:fs"
import { basename } from "node:path"
import { createRequire } from "node:module"
import { dirname, resolve, relative } from "node:path"

const relPath = (from, to) => relative(from, to)
import { fileURLToPath } from "node:url"
import { spawnSync } from "node:child_process"

const require = createRequire(import.meta.url)
const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, "../..")

const NETLIST = process.env.NETLIST ?? ""
if (!NETLIST) {
  console.error(
    "  НЕТ входа: задайте NETLIST=<путь к нетлисту или .kicad_sch KiCad>\n" +
    "  (в конвейере заказа путь приносит run_pcb.py из generate_result.json;\n" +
    "   вручную: NETLIST=out/hardware/<изделие>.net node hardware/tscircuit/build_pcb.mjs)")
  process.exit(1)
}
// Каталог результатов: по умолчанию — штатный out; для любого другого
// изделия задаётся переменной OUT_DIR (там же resolve_parts.py пишет
// resolved_parts.json — каталоги должны совпадать).
const OUT = process.env.OUT_DIR
  ? resolve(process.env.OUT_DIR)
  : resolve(ROOT, "hardware/tscircuit/out")
const TSX = resolve(OUT, "board.circuit.tsx")
const MODELS_ROOT = process.env.KICAD_MODELS_ROOT ?? resolve(HERE, "models")
const FP_ROOT = process.env.KICAD_FP_ROOT ?? "/usr/share/kicad/footprints"
// Библиотеки заказа (design/libs): сюда part_fetch кладёт символ, корпус и
// 3D EasyEDA под конкретный артикул, выбранный агентом. Корпус отсюда
// читается прямо файлом, тело — из <lib>.3dshapes, сетевой kicad-mod-cache
// для таких деталей не нужен.
const LIBS_ROOT = (() => {
  const env = process.env.KICAD_LIBS_ROOT
  if (env && existsSync(env)) return env
  const near = resolve(OUT, "../../../design/libs")
  return existsSync(near) ? near : ""
})()

// ═════════════════════════════════════════════ сети питания и заливки
//
// Вопрос «какие сети питание/земля» — семантика изделия, её приносит
// манифест компилятора (generate_result.json из schematic_from_json):
// сети с символами питания в спецификации решает агент-схемотехник.
// Манифеста нет — работает общий фолбэк по электротехнической
// фонетике имени сети (GND/VSS-семейство — земля; VIN/VBUS/VBAT/VCC/VDD
// и напряжения вида «+3V3», «5V» — питание). Никаких списков,
// привязанных к конкретному устройству, здесь нет и быть не должно.
const MANIFEST_FILE = process.env.MANIFEST ?? ""

// Сети, которые трассируются ПЕРВЫМИ: питание и земля. Остальное идёт
// последним и само обходит уже проложенную медь. Заполняется ниже —
// после разбора нетлиста (applyPowerNets).
const POWER_NETS = new Set()
// Сети с медной заливкой: земля и рельсы из того же манифеста.
const POURRED = new Set()
// Имя земли (одна сеть); остальное — рельсы.
let GROUND_NAME = ""

// Габарит платы НЕ задаётся константой: computePlacement() выводит
// его из фактических размеров корпусов, зазоров (IPC) и соотношения
// сторон sqrt(2) — для любого состава компонентов, без констант.

// ═══════════════════════════════════════════════════ правила проектирования
//
// Набор повторяет то, что в Altium задаётся в Design » Rules, а в KiCad —
// в Board Setup » Design Rules. Значения — IPC-2221B класс 2 (1 унц. медь,
// фабрика 0.1 мм / 0.1 мм): такую плату берёт любой дешёвый прототип.
//
//   Ширина дорожки:     min 0.15 мм, номинал и default 0.25 мм
//   Переход:            сверло ≥ 0.30 мм, площадка ≥ 0.60 мм (кольцо 0.15 мм)
//   Зазор медь-медь:    ≥ 0.20 мм (след, площадка, отверстие)
//   Край платы:         ≥ 0.30 мм
//   Толщина платы:      1.6 мм — стандарт FR-4
//
// Без этих ограничений tscircuit берёт фабричные умолчания своего
// circuit-json, и kicad-cli находит сверло/кольцо меньше нормы
// (via_diameter, drill_out_of_range, annular_width). Явные допуски
// снимают эти замечания ещё до autoroute.
//
// Ранее при 0.20 мм экспорт падал (0/10 файлов), что выглядело как
// «авроутер не выдерживает». Причина была не в допусках, а в рассинхроне
// версий circuit-json (см. overrides в package.json): @tscircuit/cli тянет
// circuit-json@^0.0.525, а ставилась 0.0.515 — tsci падал на импорте.
// После фикса tsci экспортирует 10/10 и при исходных 0.20/0.30 мм.
// Оставшиеся 4 замечания pad_pad_clearance (0.115–0.167 мм) — зазоры
// переходных отверстий у выводов U1/Y1; они не блокируют экспорт.
const DESIGN_RULES = {
  thickness: "1.6mm",
  minTraceWidth: "0.15mm",
  nominalTraceWidth: "0.25mm",
  defaultTraceWidth: "0.25mm",
  minViaHoleDiameter: "0.30mm",
  minViaPadDiameter: "0.60mm",
  minPadEdgeToPadEdgeClearance: "0.20mm",
  minBoardEdgeClearance: "0.30mm",
  traceClearance: "0.20mm",
}

// Уровень усилий авроутера: 1x/1.5x/2x/5x/10x/100x. 10x — «как у Altium»:
// достраивает неразведённые соединения перебором, но не уходит в часы.
const AUTOROUTER_EFFORT = process.env.AUTOROUTER_EFFORT ?? "10x"

// ═══════════════════════════════════════════════════════ разбор нетлиста KiCad

function parseNetlist(text) {  const comps = []
  // KiCad netlist versions differ: brig's "E" puts (ref)(value)(footprint)
  // adjacent, while Kristall's "D" inserts (description) between (value) and
  // (footprint). Деталь без корпуса — законный случай (модуль вне платы,
  // placement=offboard): тогда строки (footprint "…") у неё нет вовсе, и
  // поиск «до ближайшего footprint» притянул бы чужой корпус. Поэтому
  // блоки режутся по маркеру следующего (comp (ref "…").
  for (const chunk of text.split(/\(comp\s+\(ref\s+"/).slice(1)) {
    const q = chunk.indexOf('"')
    const ref = q >= 0 ? chunk.slice(0, q) : ""
    const val = chunk.match(/\(value\s+"([^"]*)"\)/)
    const fp = chunk.match(/\(footprint\s+"([^"]*)"\)/)
    comps.push({ ref, value: val ? val[1] : "",
                 footprint: fp ? fp[1] : "" })
  }

  const pinFn = new Map()
  for (const m of text.matchAll(
    /\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]+)"\)(?:\s*\(pinfunction\s+"([^"]*)"\))?/g,
  )) pinFn.set(`${m[1]}.${m[2]}`, m[3] ?? "")

  const nets = []
  // Блок сети закрывается ")))" в конце строки последнего узла, поэтому
  // искать "\n  )" бесполезно. Границы берём по позициям следующего
  // заголовка (net ...) — это устойчиво к любому отступу.
  // Net code is quoted in netlist version "E" and bare in version "D".
  const heads = [...text.matchAll(/\(net\s+\(code\s+"?(\d+)"?\)\s+\(name\s+"([^"]*)"/g)]
  for (let k = 0; k < heads.length; k++) {
    const start = heads[k].index
    const end = k + 1 < heads.length ? heads[k + 1].index : text.length
    const block = text.slice(start, end)
    const nodes = []
    for (const n of block.matchAll(
      /\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]+)"\)/g,
    )) {
      if (n[1].startsWith("#")) continue // символ питания, не компонент
      nodes.push({ ref: n[1], pin: n[2] })
    }
    if (nodes.length >= 2) {
      nets.push({ name: heads[k][2].replace(/^\//, ""), nodes })
    }
  }
  return { comps, nets, pinFn }
}

// ═══════════════════════════════════════════════════════ локальная база jlcparts

// ═══════════════════════════════════════════════════════ эмиттер TSX

let COMPONENTS = []
let NETS = []
let PINFN = new Map()
// Парт-номера приходят из resolve_parts.py: он ходит в локальные
// производные таблицы в стиле tscircuit/jlcsearch и подбирает деталь по
// категории, корпусу и номиналу. Здесь только читается готовый результат,
// своей выборки из базы здесь нет.
let PARTS = new Map()

function loadResolvedParts() {
  const p = resolve(OUT, "resolved_parts.json")
  if (!existsSync(p)) return new Map()
  try {
    const parts = JSON.parse(readFileSync(p, "utf8")).parts ?? {}
    return new Map(Object.entries(parts).map(([ref, v]) => [ref, {
      lcsc: (v.jlcpcb?.[0] ?? "").replace(/^C/, ""),
      mfr: v.mfr,
      package: v.package,
      how: v.how,
    }]))
  } catch (e) {
    console.error(`  ! не читается ${p}: ${e}`)
    return new Map()
  }
}
// ref -> {ident, abs, name} — модели, привязанные к компонентам в TSX.
// Нужно перед экспортом: нормализовать диалект STEP-файлов (см. ниже).
let MODELOF = new Map()

// ═════════════════════════════════ PCB-паспорт деталей (агент-читатель)
//
// design/parts_pcb.json — что агент-читатель даташитов выяснил о корпусе
// каждой детали (tools/part_pcb.py, роль упаковщика). Правило линии:
//   * корпус стандартный (0603, sod523, pinheader2, soic8, lqfp48…) —
//     эмитим РОДНУЮ строку tscircuit: корпус и 3D-тело строит генератор
//     footprinter; ни KiCad-файлов, ни чужой модели этой детали не нужно;
//   * нестандартный или неясный — наш KiCad-путь: локальная пара
//     символ/корпус/3D из design/libs (part_fetch.py).
// Строка генератора никогда не принимается на слово: площадки сверяются
// с локальным .kicad_mod (если он есть) после центрирования — допуск
// 0.4 мм. Не сошлось — падаем на KiCad-путь с громким предупреждением.
const PARTS_PCB_FILE = process.env.KR_PARTS_PCB
  ?? resolve(OUT, "../../../design/parts_pcb.json")

function loadPartsPcb() {
  if (!existsSync(PARTS_PCB_FILE)) return new Map()
  try {
    const parts = JSON.parse(readFileSync(PARTS_PCB_FILE, "utf8")).parts ?? {}
    return new Map(Object.entries(parts))
  } catch (e) {
    console.error(`  ! не читается ${PARTS_PCB_FILE}: ${e}`)
    return new Map()
  }
}

let PARTS_PCB = new Map()   // ref -> паспорт из parts_pcb.json
let STDOF = new Map()       // ref -> {token, src, box, note} — стандартные

// design/schematic.json — спецификация заказа (рядом с паспортом).
// Нужна только для одного: модули с "placement": "offboard" в схеме есть,
// но на плате их нет — провода идут к разъёму/клеммам. Такие refs в
// эмиссию платы не попадают.
const SPEC_FILE = process.env.KR_SPEC
  ?? resolve(OUT, "../../../design/schematic.json")

function loadOffboard() {
  const off = new Set()
  if (!existsSync(SPEC_FILE)) return off
  try {
    const spec = JSON.parse(readFileSync(SPEC_FILE, "utf8"))
    for (const p of spec.parts ?? []) {
      if (String(p.placement ?? "").toLowerCase() === "offboard")
        off.add(String(p.ref))
    }
  } catch (e) {
    console.error(`  ! не читается ${SPEC_FILE}: ${e}`)
  }
  return off
}

/**
 * Каноническая строка генератора по свободному имени корпуса (фолбэк,
 * когда паспорта нет). Правило учебника корпусов EIA/JEDEC, не список
 * деталей; итог обязан быть строкой, которую принимает footprinter —
 * иначе кандидат отвергается в fpPads().
 */
function normPackage(text) {
  if (!text) return null
  const t = String(text).toUpperCase()
  const eia = t.match(
    /(?<![0-9A-Z])(0201|0402|0603|0805|1206|1210|1812|2010|2512)(?![0-9])/)
  if (eia) return eia[1]
  let m = t.match(/SOD[-\s_]?(\d{2,3})[A-Z]?/)
  if (m) return "sod" + m[1]
  if (t.includes("MINIMELF")) return "minimelf"
  if (/(?<!MINI)MELF/.test(t)) return "melf"
  for (const name of ["SMA", "SMB", "SMC"]) {
    if (new RegExp(`(?<![A-Z])${name}(?![A-Z])`).test(t))
      return name.toLowerCase()
  }
  if (/SOT[-\s_]?23[-\s_]?5(?![0-9])/.test(t)) return "sot25"
  for (const [re, tok] of [
    ["SOT[-\\s_]?23W(?![0-9])", "sot23w"],
    ["SOT[-\\s_]?23(?![0-9])", "sot23"],
    ["SOT[-\\s_]?223(?![0-9])", "sot223"],
    ["SOT[-\\s_]?323(?![0-9])", "sot323"],
    ["SOT[-\\s_]?363(?![0-9])", "sot363"],
    ["SOT[-\\s_]?563(?![0-9])", "sot563"],
    ["SOT[-\\s_]?89(?![0-9])", "sot89"],
  ]) if (new RegExp(re).test(t)) return tok
  for (const [re, pfx] of [[/SOIC[-\s_]?(\d+)/, "soic"],
    [/TSSOP[-\s_]?(\d+)/, "tssop"], [/QFN[-\s_]?(\d+)/, "qfn"],
    [/LQFP[-\s_]?(\d+)/, "lqfp"]]) {
    m = t.match(re)
    if (m) return pfx + m[1]
  }
  if (/DPAK|TO[-\s_]?252/.test(t)) return "dpak"
  if (/TO[-\s_]?92/.test(t)) return "to92"
  if (/TO[-\s_]?220/.test(t)) return "to220"
  if (/PINHEAD|PINROW|HDR/.test(t)) {
    m = t.match(/(\d+)X(\d+)/)
    if (m) return (m[1] === "1" ? "pinheader" : "pinrow") + m[2]
    m = t.match(/(?:PINROW|PINHEAD|HDR|PIN)[-\s_]?(\d+)/)
    if (m) return "pinheader" + m[1]
  }
  return null
}

let _fpLib
function footprinterLib() {
  if (_fpLib === undefined) {
    try {
      _fpLib = require("@tscircuit/footprinter")
    } catch (e) {
      console.error(`  ! footprinter недоступен: ${e.message}`)
      _fpLib = null
    }
  }
  return _fpLib
}

/** Площадки/отверстия строки генератора: [{pin,x,y}] или null. */
function fpPads(token) {
  const f = footprinterLib()
  if (!f) return null
  try {
    const cj = f.string(token).circuitJson()
    const pads = []
    for (const e of cj) {
      if (e.type !== "pcb_smtpad" && e.type !== "pcb_plated_hole") continue
      const pin = String((e.port_hints ?? [])[0] ?? "").replace(/^pin/i, "")
      if (!/^\d+$/.test(pin)) continue
      pads.push({ pin, x: +e.x, y: +e.y })
    }
    return pads.length ? pads : null
  } catch (e) {
    return null
  }
}

/** Пронумерованные площадки локального .kicad_mod: [{pin,x,y}] или null. */
function modPads(lib, name) {
  const mod = findModPath(lib, name)
  if (!mod) return null
  const txt = readFileSync(mod, "utf8")
  const pads = []
  for (const m of txt.matchAll(
    /\(pad\s+"([^"]*)"\s+\S+\s+\S+\s*\(at\s+([-\d.]+)\s+([-\d.]+)/g,
  )) {
    if (m[1] !== "") pads.push({ pin: m[1], x: +m[2], y: +m[3] })
  }
  return pads.length ? pads : null
}

const bc = (list) => ({
  x: (Math.min(...list.map((p) => p.x))
      + Math.max(...list.map((p) => p.x))) / 2,
  y: (Math.min(...list.map((p) => p.y))
      + Math.max(...list.map((p) => p.y))) / 2,
})

/**
 * Сверка площадок генератора с локальным корпусом (допуск 0.4 мм).
 * Нет локального корпуса — принимаем строку как есть: в этом и смысл
 * родного пути, стандартному корпусу свой файл не нужен.
 *
 * Поворот корпуса — свободная величина: footprinter кладёт гребёнку 1x2
 * вдоль X, а KiCad-корпус — вдоль Y, и это не расхождение посадки.
 * Поэтому площадки сравниваются в четырёх поворотах на 90° — побеждает
 * лучший; выводы при повороте не переименовываются.
 */
function padGuard(c, fpAds) {
  const [lib, name] = c.footprint.split(":")
  const local = modPads(lib, name)
  if (!local) return { ok: true, note: "локального корпуса нет" }
  if (local.length !== fpAds.length)
    return { ok: false, why: `число площадок ${local.length}≠${fpAds.length}` }
  const a = bc(fpAds), b = bc(local)
  const byPin = new Map(local.map((p) => [p.pin, p]))
  const missing = fpAds.filter((p) => !byPin.has(p.pin)).map((p) => p.pin)
  if (missing.length)
    return { ok: false, why: `нет выводов ${missing.join(", ")}` }
  const worst = (rot) => {
    let m = 0
    for (const p of fpAds) {
      const q = byPin.get(p.pin)
      const dx = p.x - a.x, dy = p.y - a.y
      const quad = [[dx, dy], [dy, -dx], [-dx, -dy], [-dy, dx]][rot]
      m = Math.max(m, Math.abs(quad[0] - (q.x - b.x)),
                   Math.abs(quad[1] - (q.y - b.y)))
    }
    return m
  }
  let best = Infinity, bestRot = 0
  for (let rot = 0; rot < 4; rot++) {
    const w = worst(rot)
    if (w < best) { best = w; bestRot = rot }
  }
  return best <= 0.4
    ? { ok: true, note: `площадки сошлись (${fpAds.length}, поворот ${bestRot * 90}°)` }
    : { ok: false, why: `площадки расходятся на ${best.toFixed(2)} мм (лучший поворот ${bestRot * 90}°)` }
}

/** Габарит окна детали по площадкам/отверстиям генератора. */
function fpBox(fpAds) {
  const xs = fpAds.map((p) => p.x), ys = fpAds.map((p) => p.y)
  const xmin = Math.min(...xs), xmax = Math.max(...xs)
  const ymin = Math.min(...ys), ymax = Math.max(...ys)
  return {
    w: xmax - xmin + 1.2,
    h: ymax - ymin + 1.2,
    cx: (xmin + xmax) / 2,
    cy: (ymin + ymax) / 2,
  }
}

/**
 * Решение «стандартный корпус» по каждой детали: паспорт → подбор →
 * имя корпуса. Каждый кандидат проверяется генератором и сверкой
 * площадок. Победил — родной путь; нет — KiCad-путь с предупреждением.
 * Ответ паспорта авторитетен: не сошёлся по площадкам — не «улучшаем»
 * эвристикой, а сразу падаем на локальную пару.
 */
function collectStandards() {
  const out = new Map()
  for (const c of COMPONENTS) {
    const info = PARTS_PCB.get(c.ref) ?? {}
    const part = PARTS.get(c.ref) ?? {}
    const [, name] = c.footprint.split(":")
    const cands = []
    if (info.package_std) cands.push(["паспорт", info.package_std])
    if (part.package) cands.push(["подбор", normPackage(part.package)])
    cands.push(["корпус", normPackage(name)])
    for (const [src, tok] of cands) {
      if (!tok) continue
      const ads = fpPads(tok)
      if (!ads) continue
      const g = padGuard(c, ads)
      if (!g.ok) {
        console.warn(`  ! [паспорт] ${c.ref}: строка «${tok}» (${src}) `
          + `не сошлась с локальным корпусом: ${g.why} — KiCad-путь`)
        if (src === "паспорт") break
        continue
      }
      out.set(c.ref, { token: tok, src, box: fpBox(ads), note: g.note })
      break
    }
  }
  return out
}

const j = (v) => JSON.stringify(String(v))
const num = (v) => Number(v)

// ═════════════════════════════════════════ заполна сетей питания

/**
 * Заполнение POWER_NETS / POURRED по факту нетлиста.
 *
 * Порядок: (1) манифест компилятора схемы — там сети питания
 * перечислены агентом-схемотехником (поле power в спецификации);
 * (2) общий фолбэк по имени сети: земля — это GND/VSS-семейство,
 * питание — VIN/VBUS/VBAT/VCC/VDD и напряжения вида «+3V3», «12V».
 * Семантика самая общая электротехническая, привязки к устройству нет.
 */
function applyPowerNets(nets) {
  const names = nets.map((n) => String(n.name))
  let ground = ""
  let power = []

  if (MANIFEST_FILE) {
    try {
      const mf = JSON.parse(readFileSync(MANIFEST_FILE, "utf8"))
      ground = String(mf.ground ?? "")
      power = (mf.power_nets ?? []).map(String).filter((x) => x && x !== ground)
      if (mf.title) TITLE = String(mf.title)
      console.log(`  [питание] манифест: земля «${ground || "нет"}», рельсы ${power.length}`)
    } catch (e) {
      console.error(`  ! манифест ${MANIFEST_FILE} не читается (${e}) — фолбэк по именам`)
    }
  }

  if (!ground) {
    const gRe = /(^|[^A-Z0-9])(GND|VSS|AGND|PGND)([^A-Z0-9]|$)/i
    ground = names.find((n) => gRe.test(n)) ?? ""
  }
  if (!power.length) {
    const pRe = /^(vin|vbus|vbat|vcc|vdd)[^A-Z0-9]?|^[+-]?\d+(\.\d+)?v(\d+)?$/i
    power = names.filter((n) => pRe.test(n) && n !== ground)
  }

  POWER_NETS.clear()
  if (ground) POWER_NETS.add(ground)
  for (const p of power) POWER_NETS.add(p)
  POURRED.clear()
  for (const n of POWER_NETS) POURRED.add(tscNetName(n))
  GROUND_NAME = ground
  console.log(`  [питание] земля «${ground || "не найдена"}», ` +
    `рельсы: ${[...power].join(", ") || "нет"}`)
}

let TITLE = ""

/**
 * Имя сети -> допустимое имя для tscircuit.
 * Ядро отклоняет '+' и '-' в именах сетей: "Net names cannot contain
 * '+' or '-'". Поэтому +3V3_DIG -> V3V3_DIG, а дефис заменяется на '_'.
 */
function tscNetName(name) {
  return String(name)
    .replace(/\+/g, "P")   // +3V3 -> P3V3
    .replace(/-/g, "_")    // IF-38M -> IF_38M
    .replace(/[^A-Za-z0-9_]/g, "_")
}

/**
 * Путь к 3D-модели корпуса.
 *
 * Модель лежит в зеркале packages3d/pcb-3d и приведена к STL
 * (см. step2stl.py: tscircuit грузит модель как mesh и понимает stl,
 * но не подхватывает step). Путь берётся из строки (model ...) самого
 * корпуса — имена моделей не совпадают с именами корпусов
 * (SOT-223-3_TabPin2 -> SOT-223), поэтому угадывать нельзя.
 */
/**
 * Файл корпуса: системные библиотеки KiCad, затем design/libs заказа.
 */
function findModPath(lib, name) {
  for (const r of [FP_ROOT, ...(LIBS_ROOT ? [LIBS_ROOT] : [])]) {
    const p = resolve(r, `${lib}.pretty`, `${name}.kicad_mod`)
    if (existsSync(p)) return p
  }
  return null
}

/**
 * Файлы тела детали для корпуса: {step, stl} — что нашлось.
 *
 * Сначала проектные библиотеки заказа (design/libs: EasyEDA кладёт
 * .step/.wrl рядом с корпусом), затем зеркало packages3d в STL. Путь
 * берётся из строки (model ...) файла .kicad_mod. Но у части корпусов
 * её нет вовсе — например TestPoint_Pad_D1.0mm, у которого строка
 * модели просто опущена. Тогда tscircuit подставляет в circuit.json
 * ссылку на kicad-mod-cache.tscircuit.com, и сайт начинает ходить в
 * сеть, чего быть не должно.
 *
 * Поэтому при отсутствии строки модели проверяется соглашение
 * packages3d: <Lib>.3dshapes/<Name>.step. Проверка идёт по факту
 * наличия файла, а не по списку корпусов, так что переносится на
 * любую схему.
 *
 * Почему пара. Экспортёры tscircuit читают форматы по-разному:
 *   * board.glb берёт приоритетом model_stl_url и локальный STL читает
 *     как настоящую геометрию — а локальный STEP не умеет и рисует
 *     коробку-заглушку (24 вершины, проверено);
 *   * board.step собирает настоящие B-Rep тела только из model_step_url,
 *     STL там превращается в коробку.
 * Поэтому у детали с STEP и STL в TSX идут оба адреса.
 *
 * Ориентация STL в паре обязана совпадать с STEP (тело в +z, низ у нуля):
 * при наличии stepUrl сборщик glb читает STL переводом STEP-ветки
 * (y=+z), и зеркальный STL встал бы вверх ногами — деталь ушла бы внутрь
 * платы. Зеркальные STL — только у системного зеркала packages3d БЕЗ
 * stepUrl (там действует USB-ветка, y=-z). Файлы заказа готовит
 * part_fetch.py (NORMALIZE_Z=1, без зеркала); пары сверяются ещё до
 * экспорта — modelPairOrientation в main.
 *
 * .wrl в паре не участвует: его не читает ни один из двух экспортёров.
 */
function modelFor(libId, name) {
  const mod = findModPath(libId, name)
  const cands = []
  if (mod) {
    for (const m of readFileSync(mod, "utf8").matchAll(/\(model\s+"([^"]+)"/g)) {
      cands.push(m[1].replace(/^\$\{[A-Z0-9_]+\}\//, ""))
    }
  }
  cands.push(`${libId}.3dshapes/${name}.step`)
  const roots = [
    ...(LIBS_ROOT ? [LIBS_ROOT, resolve(LIBS_ROOT, "../..")] : []),
    MODELS_ROOT,
  ]
  const find = (exts) => {
    for (const c of cands) {
      const base = c.replace(/\.(step|stp|wrl|vrml|stl)$/i, "")
      for (const ext of exts) {
        for (const r of roots) {
          const p = resolve(r, base + ext)
          if (existsSync(p)) return p
        }
      }
    }
    return null
  }
  const step = find([".step", ".stp"])
  const stl = find([".stl"])
  if (!step && !stl) return null
  return { step, stl }
}

/** Идентификатор импорта для пути к модели. */
function importIdent(absPath) {
  return "m" + relPath(MODELS_ROOT, absPath).replace(/[^A-Za-z0-9]/g, "_")
}

/**
 * Нормализация STEP-диалекта SolidWorks (его пишет EasyEDA).
 *
 * Сборщик STEP внутри tsci (circuit-json-to-step) разбирает файл простым
 * токенизатором и требует закрывающую скобку сущности вплотную к «;»
 * (`…);`). SolidWorks пишет `) ;` (пробел перед точкой с запятой) и
 * выгружает файл с CRLF — на таких моделях сборщик падает в
 * «Could not parse entity», предупреждение «Failed to merge STEP model»
 * уходит в stderr, и тело детали молча не попадает в board.step
 * (проверено: R0603 и SOD-523 L1.2-W0.8-H0.7-LS1.6).
 *
 * Правка меняет только пробелы и переводы строк — геометрия байт в байт
 * та же, файл остаётся валидным STEP для KiCad, FreeCAD и любого другого
 * потребителя. Идемпотентна: уже нормализованный файл не переписывается.
 *
 * Возвращает true, если файл изменён.
 */
function normalizeStepFile(path) {
  if (!/\.(step|stp)$/i.test(path)) return false
  let txt
  try {
    txt = readFileSync(path, "utf8")
  } catch {
    return false
  }
  if (!txt.includes("\r") && !/\)[ \t]+;[ \t]*$/m.test(txt)) return false
  writeFileSync(path, txt.replace(/\r\n?/g, "\n").replace(/\)[ \t]+;[ \t]*$/gm, ");"))
  return true
}

/**
 * Минимум z по «сырым» точкам STEP-модели (null, если точек не нашлось).
 *
 * Зачем. Плоскость z=0 модели экспортёры кладут на поверхность платы, а
 * модель, целиком лежащую выше нуля, сборщик board.step задирает на
 * толщину платы в воздух — его ветка «top layer» считает
 * halfThickness − minZ + offsetZ. Модели EasyEDA приходят в нужной
 * конвенции (низ на ≈−0.01: у R0603 прямо в точках, у SOD-523 — через
 * внутреннее размещение), поэтому проверка ловит только выбивающиеся
 * выгрузки. Важно: точки читаются «сырыми», без размещений внутри файла,
 * — ветку сборщика выбирает ровно их минимум (см. adjustTransformForPlacement).
 */
function stepMinZ(path) {
  let txt
  try {
    txt = readFileSync(path, "utf8")
  } catch {
    return null
  }
  let zmin = null
  for (const m of txt.matchAll(/CARTESIAN_POINT\s*\(\s*'?[^']*'?\s*,\s*\(([^)]*)\)/g)) {
    const p = m[1].split(",")
    if (p.length !== 3) continue
    const z = Number(p[2])
    if (Number.isFinite(z) && (zmin === null || z < zmin)) zmin = z
  }
  return zmin
}

/**
 * Диапазон z бинарного STL: [zmin, zmax] или null, если не разобрать.
 *
 * Нужен для сверки ориентации пары STL+STEP до экспорта: дешевле, чем
 * ждать экспорт и разбирать board.glb. Конвенция пары — STL совпадает
 * со STEP (тело в +z, низ у нуля), см. modelFor.
 */
function stlZRange(path) {
  let buf
  try {
    buf = readFileSync(path)
  } catch {
    return null
  }
  if (buf.length < 84 || buf.subarray(0, 5).toString() === "solid") return null
  const n = buf.readUInt32LE(80)
  if (84 + n * 50 !== buf.length) return null
  let zmin = Infinity, zmax = -Infinity
  for (let i = 0; i < n; i++) {
    const off = 84 + 50 * i
    for (const k of [2, 5, 8, 11]) {          // z нормали и трёх вершин
      const z = buf.readFloatLE(off + 4 * k)
      if (z < zmin) zmin = z
      if (z > zmax) zmax = z
    }
  }
  return Number.isFinite(zmin) ? [zmin, zmax] : null
}

/**
 * Центр выводов корпуса из .kicad_mod.
 *
 * Именно выводы, а не courtyard: courtyard у KiCad бывает смещён
 * относительно начала координат (у PinHeader_1x06 он центрирован в
 * y=6.35, тогда как начало координат — на первом выводе).
 */
function padCenter(libId, name) {
  const mod = findModPath(libId, name)
  if (!mod) return null
  const txt = readFileSync(mod, "utf8")
  const pts = []
  for (const m of txt.matchAll(
    /\(pad\s+"?[^"\s]*\S"?\s+\S+\s+\S+\s*\(at\s+([-\d.]+)\s+([-\d.]+)/g,
  )) {
    pts.push([+m[1], +m[2]])
  }
  if (!pts.length) return null
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1])
  return {
    x: (Math.min(...xs) + Math.max(...xs)) / 2,
    y: (Math.min(...ys) + Math.max(...ys)) / 2,
  }
}

/**
 * Габариты корпуса по посадочному месту (courtyard) из .kicad_mod.
 * Возвращает {w, h} в мм.
 */
function footprintSize(libId, name) {
  const mod = findModPath(libId, name)
  if (!mod) return { w: 5, h: 5, cx: 0, cy: 0 }
  const txt = readFileSync(mod, "utf8")
  const pts = []
  // В .kicad_mod слой указывается ПОСЛЕ start/end, поэтому блоки режем
  // целиком и проверяем наличие F.CrtYd, а не ищем всё одним regex.
  for (const m of txt.matchAll(/\((fp_line|fp_rect|fp_poly)\b([\s\S]*?)\n\t\)/g)) {
    const body = m[2]
    if (!/\(layer\s+"F\.CrtYd"\)/.test(body)) continue
    const s = body.match(/\(start\s+([-\d.]+)\s+([-\d.]+)\)/)
    const e = body.match(/\(end\s+([-\d.]+)\s+([-\d.]+)\)/)
    if (s) pts.push([+s[1], +s[2]])
    if (e) pts.push([+e[1], +e[2]])
  }
  if (!pts.length) return { w: 5, h: 5, cx: 0, cy: 0 }
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1])
  // cx/cy — смещение центра courtyard относительно начала координат корпуса.
  // У SMA edge-mount, USB-B и штыревых разъёмов courtyard не центрирован, и
  // без этого смещения shelf-упаковка ставит соседей внахлёст.
  return {
    w: Math.max(...xs) - Math.min(...xs),
    h: Math.max(...ys) - Math.min(...ys),
    cx: (Math.min(...xs) + Math.max(...xs)) / 2,
    cy: (Math.min(...ys) + Math.max(...ys)) / 2,
  }
}

/**
 * Явное размещение компонентов — shelf-упаковка по строкам.
 *
 * Зачем не авторазмещение: tscircuit утрамбовал 73 компонента в пятно
 * 97x55 мм с минимальным расстоянием между центрами 2.46 мм, из-за чего
 * фаза fanout не могла вывести выводы к границе.
 *
 * Зачем не жёсткая сетка: у ESP32 courtyard 48x41 мм (в габарит входит
 * keepout антенны), и сетка по максимальному корпусу давала плату
 * 234x224 мм = 52 400 мм². Ёмкостная сетка авроутера строится по всей
 * площади, отсюда 1.8 ГБ памяти и исчерпание итераций.
 *
 * Shelf-упаковка: корпуса сортируются по высоте, раскладываются в строку
 * слева направо, строка закрывается по достижении целевой ширины. Высота
 * строки равна самому высокому корпусу в ней. Итог — плотный
 * прямоугольник при любом распределении габаритов, без магических чисел.
 *
 * Целевая ширина берётся из суммарной площади корпусов и желаемого
 * соотношения сторон sqrt(2) (A-серия), тоже без констант «на глаз».
 */
const GRID_CLEARANCE = 4.0   // мм зазор между корпусами
const BOARD_MARGIN = 8.0    // мм от края платы до меди
const BOARD_ASPECT = Math.SQRT2   // ширина/высота платы

function computePlacement() {
  const sizes = new Map()
  for (const c of COMPONENTS) {
    const std = STDOF.get(c.ref)
    if (std) {
      // стандартный корпус: габарит по площадкам генератора — своего
      // .kicad_mod у него может не быть вовсе
      sizes.set(c.ref, std.box)
      continue
    }
    const [lib, name] = c.footprint.split(":")
    sizes.set(c.ref, footprintSize(lib, name))
  }
  const clear = Number(process.env.GRID_CLEARANCE ?? GRID_CLEARANCE)
  const margin = Number(process.env.BOARD_MARGIN ?? BOARD_MARGIN)

  // Целевая ширина: из площади корпусов + зазоры + соотношение сторон.
  const area = COMPONENTS.reduce((a, c) => {
    const s = sizes.get(c.ref)
    return a + (s.w + clear) * (s.h + clear)
  }, 0)
  const targetW = Math.sqrt(area * BOARD_ASPECT)

  // Убывание высоты: высокие корпуса ложатся в начало строк, где выше
  // требуемая высота строки, и не оставляют дыр.
  const order = [...COMPONENTS].sort(
    (a, b) => sizes.get(b.ref).h - sizes.get(a.ref).h,
  )

  const place = new Map()
  let rowX = 0
  let rowY = 0
  let rowH = 0
  let rowW = 0
  for (const c of order) {
    const s = sizes.get(c.ref)
    if (rowW > 0 && rowW + clear + s.w > targetW) {
      // Строка закрыта — следующий корпус начинает новую.
      rowY -= rowH + clear
      rowX = 0
      rowH = 0
      rowW = 0
    }
    place.set(c.ref, { x: rowX + s.w / 2 - s.cx, y: -(rowY + s.h / 2) - s.cy })
    rowX += s.w + clear
    rowW = rowX
    rowH = Math.max(rowH, s.h)
  }

  // Ограничивающий прямоугольник — по ГАБАРИТАМ корпусов, а не по
  // координатам центров: у ESP32 courtyard 48x41 мм при центре, стоящем
  // на краю блока, площадки вылезали за край платы на 0 мм.
  let minX = Infinity, maxX = -Infinity
  let minY = Infinity, maxY = -Infinity
  for (const c of order) {
    const s = sizes.get(c.ref)
    const p = place.get(c.ref)
    minX = Math.min(minX, p.x + s.cx - s.w / 2)
    maxX = Math.max(maxX, p.x + s.cx + s.w / 2)
    minY = Math.min(minY, p.y + s.cy - s.h / 2)
    maxY = Math.max(maxY, p.y + s.cy + s.h / 2)
  }

  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2
  for (const p of place.values()) {
    p.x -= cx
    p.y -= cy
  }

  const width = maxX - minX + 2 * margin
  const height = maxY - minY + 2 * margin
  return { place, width, height }
}

function emitTsx() {
  const L = []
  L.push("// СГЕНЕРИРОВАНО автоматически из " + relPath(ROOT, NETLIST))
  L.push("// Не редактировать вручную: источник истины — принципиальная схема KiCad.")
  L.push("// Обработку (автотрассировку, 3D, экспорт) выполняет официальный tsci.")
  L.push("")
  const { place, width, height } = computePlacement()
  L.push(`const BOARD_W = ${width}`)
  L.push(`const BOARD_H = ${height}`)
  L.push("")

  // ---- 3D-модели: импортируем каждую один раз ----
  // Позицию и ориентацию tsci считает сам: плоскость z=0 модели ложится
  // на поверхность платы, тело выравнивается по контакту. Проверка
  // посадки после экспорта — hardware/tscircuit/check_models3d.py.
  // Модель эмитится парой URL (STL для glb, STEP для step) — почему, см.
  // комментарий у modelFor.
  const modelOf = new Map()
  MODELOF = modelOf
  const imported = new Map()  // abs -> ident
  const importOf = (abs) => {
    let ident = imported.get(abs)
    if (!ident) {
      ident = importIdent(abs)
      imported.set(abs, ident)
      L.push(`import ${ident} from ${j(relPath(OUT, abs))}`)
    }
    return ident
  }
  for (const c of COMPONENTS) {
    if (STDOF.has(c.ref)) continue // стандартный: тело строит сам tscircuit
    const [lib, name] = c.footprint.split(":")
    const mdl = modelFor(lib, name)
    if (!mdl) continue
    modelOf.set(c.ref, {
      stl: mdl.stl ? importOf(mdl.stl) : null,
      step: mdl.step ? importOf(mdl.step) : null,
      stlAbs: mdl.stl,
      stepAbs: mdl.step,
      name,
    })
  }
  L.push("")

  L.push("export default () => (")
  L.push("  <board")
  L.push(`    width={\`\${BOARD_W}mm\`}`)
  L.push(`    height={\`\${BOARD_H}mm\`}`)
  L.push("    layers={4}")
  L.push(`    thickness="${DESIGN_RULES.thickness}"`)
  L.push(`    minTraceWidth="${DESIGN_RULES.minTraceWidth}"`)
  L.push(`    nominalTraceWidth="${DESIGN_RULES.nominalTraceWidth}"`)
  L.push(`    defaultTraceWidth="${DESIGN_RULES.defaultTraceWidth}"`)
  L.push(`    minViaHoleDiameter="${DESIGN_RULES.minViaHoleDiameter}"`)
  L.push(`    minViaPadDiameter="${DESIGN_RULES.minViaPadDiameter}"`)
  L.push(`    minPadEdgeToPadEdgeClearance="${DESIGN_RULES.minPadEdgeToPadEdgeClearance}"`)
  L.push(`    minBoardEdgeClearance="${DESIGN_RULES.minBoardEdgeClearance}"`)
  L.push(`    autorouter={{ traceClearance: "${DESIGN_RULES.traceClearance}" }}`)
  L.push(`    autorouterEffortLevel="${AUTOROUTER_EFFORT}"`)
  L.push("  >")

  // Фаза 0 — обычная трассировка.
  //
  // Здесь была фаза autorouter="fanout" (вывод силовых соединений на
  // выделенные плоскости) — она помогала для термоплощадки ESP32, но
  // требует, чтобы КАЖДЫЙ вывод КАЖДОГО компонента вышел к границе
  // breakout. На плате с двумя-выводными пассивками это неверно: фаза
  // требовала 41 вывод и выдавала "only 18 of 41 connections could escape
  // to the breakout boundary". Силовые сети вместо этого соединяет
  // медная заливка, объявленная ниже.
  L.push(`    <autoroutingphase phaseIndex={0} />`)

  // ---- компоненты ----
  for (const c of COMPONENTS) {
    const pins = [...new Set(NETS.flatMap((n) => n.nodes.filter((x) => x.ref === c.ref).map((x) => x.pin)))]
    const labels = {}
    for (const p of pins) {
      const fn = (PINFN.get(`${c.ref}.${p}`) ?? "").replace(/[^A-Za-z0-9_]/g, "_").replace(/^(\d)/, "P$1")
      labels[p] = fn || `P${p}`
    }
    // Корпус из проектной библиотеки заказа (EasyEDA): tsci умеет
    // читать .kicad_mod прямо по пути — сетевой kicad-mod-cache для
    // таких деталей не нужен. Путь абсолютный: при экспорте статические
    // пути не привязываются к каталогу запуска (проверено: "./…"
    // нормализуется и открывается как есть). Системные корпуса идут
    // как kicad:<lib>/<name>.
    const [flib, fname] = c.footprint.split(":")
    const std = STDOF.get(c.ref)
    let fp
    if (std) {
      // Родной путь: строка генератора — корпус и 3D-тело строит
      // tscircuit (footprinter). Локальные .kicad_mod/.step этой детали
      // не нужны вовсе; строка прошла guard по площадкам (collectStandards).
      fp = std.token
    } else {
      const fmod = findModPath(flib, fname)
      fp = `kicad:${c.footprint.replace(":", "/")}`
      if (fmod && LIBS_ROOT && fmod.startsWith(LIBS_ROOT)) {
        fp = fmod
      }
    }
    const part = PARTS.get(c.ref)
    const m = modelOf.get(c.ref)
    const pos = place.get(c.ref) ?? { x: 0, y: 0 }
    L.push(`    <chip`)
    L.push(`      name={${j(c.ref)}}`)
    L.push(`      footprint={${JSON.stringify(fp)}}`)
    L.push(`      pcbX={${pos.x}}`)
    L.push(`      pcbY={${pos.y}}`)
    L.push(`      pinLabels={${JSON.stringify(labels)}}`)
    L.push(`      manufacturerPartNumber={${JSON.stringify(c.value)}}`)
    if (part) L.push(`      supplierPartNumbers={{ jlcpcb: [${JSON.stringify("C" + part.lcsc)}] }}`)
    if (m) {
      // Тело детали — обоими адресами, потому что экспортёры tscircuit
      // читают разные форматы:
      //   * board.glb — приоритетом model_stl_url (локальный STL читается
      //     как настоящая геометрия; локальный STEP он не конвертирует и
      //     рисует коробку-заглушку);
      //   * board.step — настоящие B-Rep тела только из model_step_url
      //     (STL там тоже коробка).
      //   * ориентация STL = ориентация STEP (в паре glb применяет к STL
      //     перевод STEP-ветки; почему — комментарий у modelFor).
      // Позиционирование целиком на tscircuit: плоскость z=0 модели он
      // кладёт на поверхность платы (проверено замером GLB против
      // circuit.json, см. check_models3d.py) — собственные сдвиги
      // (positionOffset) сдвинули бы модель из центра на свою величину.
      const props = m.stl ? [`modelUrl={${m.stl}}`] : [`modelUrl={${m.step}}`]
      if (m.stl && m.step) props.push(`stepUrl={${m.step}}`)
      L.push(`      cadModel={<cadmodel ${props.join(" ")} />}`)
    }
    L.push(`    />`)
  }

  // ---- сети как <net> + source-only трассы ----
  // Раньше каждая сеть разводилась ЗВЕЗДОЙ: один вывод — «голова»,
  // к нему тянутся трассы от всех остальных. Для GND (46 узлов) это
  // 45 длинных трасс через всю плату, и авроутер на этом ломается:
  // «target obstacle has no bordering routing edge».
  //
  // Идиоматичный способ tscircuit — объявить <net> и подключить к нему
  // каждый вывод ОТДЕЛЬНОЙ короткой source-only трассой. Тогда:
  //   * длина меди определяется близостью вывода к своему участку заливки;
  //   * силовые сети можно завершать на медной плоскости (fanoutPourNetMap);
  //   * авроутер раскладывает трассы по фазам, а не пытается протянуть
  //     45 штук от одной точки.
  const netNames = NETS.map((n) => tscNetName(n.name))
  L.push("")
  for (const net of NETS) {
    const flags = []
    if (POWER_NETS.has(net.name)) flags.push('routingPhaseIndex={0}')
    if (GROUND_NAME && net.name === GROUND_NAME) flags.push("isGroundNet")
    else if (POWER_NETS.has(net.name)) flags.push("isPowerNet")
    L.push(`    <net name=${j(tscNetName(net.name))}${flags.length ? " " + flags.join(" ") : ""} />`)
  }

  // ---- трассы: каждый вывод -> своя сеть ----
  // Для сетей, у которых объявлена медная заливка, трассы НЕ создаются:
  // заливка соединяет их площадки сама, а трассы лишь добавляют авроутеру
  // работу. Один GND давал 46 трасс, +3V3_DIG — ещё 15; вместе это
  // почти половина задачи и причина исчерпания итераций.
  let i = 0
  for (const net of NETS) {
    if (POURRED.has(tscNetName(net.name))) continue
    for (const nd of net.nodes) {
      i++
      L.push(`    <trace name=${j(`${tscNetName(net.name)}_${i}`)} from=${j(`${nd.ref}.pin${nd.pin}`)} to=${j("net." + tscNetName(net.name))} />`)
    }
  }

  // ---- заливки ----
  // Земля разводится заливкой, а не десятками трасс: это и электрически
  // правильнее (низкое сопротивление), и снимает нагрузку с авроутера.
  // Какие сети считать землёй и рельсами — решено агентом (манифест):
  // ground → нижний слой + внутренний слой, остальные рельсы — верхний.
  // Отступы заливки — из той же таблицы правил, что у платы. По умолчанию
  // у CopperPour clearance 0.2 мм, а к краю платы правило строже, и DRC
  // ловит «copper pour violates copper-to-board-edge clearance».
  const pourMargins = `clearance="${DESIGN_RULES.traceClearance}" `
    + `boardEdgeMargin="${DESIGN_RULES.minBoardEdgeClearance}"`
  if (GROUND_NAME) {
    L.push(`    <copperpour connectsTo=${j("net." + tscNetName(GROUND_NAME))} layer="bottom" ${pourMargins} />`)
    L.push(`    <copperpour connectsTo=${j("net." + tscNetName(GROUND_NAME))} layer="inner1" ${pourMargins} />`)
  }
  // Остальные силовые сети — на верхний слой, разводятся трассами
  // в фазе 0 (см. routingPhaseIndex выше).
  for (const n of [...POWER_NETS].filter((x) => x !== GROUND_NAME)) {
    const tn = tscNetName(n)
    if (netNames.includes(tn)) {
      L.push(`    <copperpour connectsTo=${j("net." + tn)} layer="top" ${pourMargins} />`)
    }
  }

  L.push("  </board>")
  L.push(")")
  return L.join("\n") + "\n"
}

// ═══════════════════════════════════════════════════════ экспорт через tsci

function tsci(args) {
  const r = spawnSync("npx", ["--no-install", "tsci", ...args], {
    cwd: ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024,
  })
  const out = ((r.stdout ?? "") + (r.stderr ?? "")).trim()
  // Тела деталей в board.step: если сборщик не разобрал чей-то STEP, tsci
  // пишет «Failed to merge STEP model …» и молча пропускает модель — код
  // выхода при этом нулевой. Молчать нельзя: 3D окажется неполным.
  const mergeWarn = out.split("\n").filter((l) => l.includes("Failed to merge STEP model"))
  for (const w of mergeWarn.slice(0, 8)) console.error("      ! " + w.trim())
  if (mergeWarn.length > 8) console.error(`      ! … и ещё ${mergeWarn.length - 8}`)
  const fatal = /SyntaxError|Cannot find module|ERR_MODULE_NOT_FOUND/.test(out)
  if (r.status !== 0 || fatal) {
    console.error(`  ! tsci ${args.join(" ")} -> ${r.status}`)
    console.error(out.split("\n").slice(0, 12).map((l) => "      " + l).join("\n"))
    return false
  }
  return true
}

// ═══════════════════════════════════════════════════════ main

function main() {
  mkdirSync(OUT, { recursive: true })

  console.log("=".repeat(74))
  console.log(`${TITLE || "ИЗДЕЛИЕ"} -> tscircuit (эмиттер TSX + экспорт tsci)`)
  console.log("=".repeat(74))
  console.log(`\n  [вход] ${NETLIST}`)

  const { comps, nets, pinFn } = parseNetlist(readFileSync(NETLIST, "utf8"))
  COMPONENTS = comps.filter((c) => !c.ref.startsWith("#"))
  NETS = nets
  PINFN = pinFn

  // ---- модули вне платы (placement=offboard): снять с эмиссии платы ----
  const OFFBOARD = loadOffboard()
  if (OFFBOARD.size) {
    COMPONENTS = COMPONENTS.filter((c) => !OFFBOARD.has(c.ref))
    NETS = NETS.map((n) => ({
      ...n,
      nodes: n.nodes.filter((nd) => !OFFBOARD.has(nd.ref)),
    })).filter((n) => n.nodes.length > 0)
    console.log(`  [offboard] вне платы (на PCB не ставятся): `
      + `${[...OFFBOARD].join(", ")}`)
  }
  applyPowerNets(NETS)

  console.log(`  [netlist] компонентов ${COMPONENTS.length}, сетей ${NETS.length}`)

  // ---- парт-номера из локального подбора ----
  PARTS = loadResolvedParts()
  const withPpn = COMPONENTS.filter((c) => PARTS.get(c.ref)?.lcsc)
  console.log(`  [база] ${relPath(ROOT, resolve(OUT, "resolved_parts.json"))}`)
  console.log(`         парт-номеров подобрано ${withPpn.length} из ${COMPONENTS.length}`)
  if (!withPpn.length) {
    console.log("         нет файла подбора или он пуст — запустите "
      + "hardware/tscircuit/resolve_parts.py")
  }
  const noPpn = COMPONENTS.filter((c) => !PARTS.get(c.ref)?.lcsc)
  if (noPpn.length) {
    const byHow = {}
    for (const c of noPpn) {
      const v = PARTS.get(c.ref)
      const k = v ? (v.how ?? "неизвестно") : "нет данных"
      byHow[k] = (byHow[k] ?? 0) + 1
    }
    console.log(`         без парт-номера ${noPpn.length}: ` +
      Object.entries(byHow).map(([k, n]) => `${k} — ${n}`).join("; "))
  }

  // ---- паспорт деталей: стандартные корпуса до расчёта размещения ----
  PARTS_PCB = loadPartsPcb()
  STDOF = collectStandards()

  // ---- проверка корпусов локально, до эмита ----
  const custom = COMPONENTS.filter((c) => !STDOF.has(c.ref))
  const missingFp = []
  for (const c of custom) {
    const [lib, name] = c.footprint.split(":")
    if (!findModPath(lib, name)) missingFp.push(`${c.ref}: ${c.footprint}`)
  }
  if (missingFp.length) {
    console.error(`\n  ОШИБКА: нет ${missingFp.length} корпусов локально (нестандартные детали):`)
    for (const m of missingFp.slice(0, 20)) console.error("      " + m)
    process.exit(1)
  }
  console.log(`  [паспорт] ${relPath(ROOT, PARTS_PCB_FILE)}`)
  if (STDOF.size) {
    console.log(`         стандартных корпусов ${STDOF.size}/${COMPONENTS.length} `
      + `— родной генератор tscircuit: `
      + [...STDOF.entries()].map(([r, v]) => `${r}:${v.token}`).join(" "))
  }
  if (custom.length) {
    console.log(`         нестандартных ${custom.length} `
      + `— наш KiCad-путь (локальная пара)${LIBS_ROOT ? ", включая design/libs" : ""}`)
  }

  // ---- эмитт TSX ----
  writeFileSync(TSX, emitTsx())
  // Трассы создаются не для всех сетей: у сетей с медной заливкой
  // площадки соединяет заливка. Считаем по факту.
  const nTraces = NETS
    .filter((n) => !POURRED.has(tscNetName(n.name)))
    .reduce((a, n) => a + n.nodes.length, 0)
  console.log(`  [эмитт] ${TSX}`)
  console.log(`          компонентов ${COMPONENTS.length}, трасс ${nTraces}`)

  // ---- контроль STEP-моделей до экспорта ----
  //
  // Диалект SolidWorks (CRLF и «) ;») сборщик STEP в tsci не разбирает —
  // это правит part_fetch.py при скачивании, здесь страховка на старые
  // заказы. Вторая проверка: модель, целиком лежащая выше z=0, в
  // board.step задирается на толщину платы в воздух — низ модели должен
  // быть чуть ниже нуля (у моделей EasyEDA так и есть).
  const fixedSteps = []
  const floatingSteps = []
  const stepFiles = new Set(
    [...MODELOF.values()].map((v) => v.stepAbs).filter(Boolean),
  )
  for (const p of stepFiles) {
    if (normalizeStepFile(p)) fixedSteps.push(relPath(ROOT, p))
    const zmin = stepMinZ(p)
    if (zmin !== null && zmin >= 0) floatingSteps.push(`${relPath(ROOT, p)} (zmin ${zmin})`)
  }
  if (fixedSteps.length) {
    console.log(`  [STEP] нормализован диалект SolidWorks: ${fixedSteps.length} файл(ов)`)
    for (const f of fixedSteps.slice(0, 6)) console.log(`         ${f}`)
  }
  if (floatingSteps.length) {
    console.warn(`  ! [STEP] ${floatingSteps.length} моделей целиком выше z=0 — в board.step уедут над платой`)
    for (const f of floatingSteps.slice(0, 6)) console.warn(`         ${f}`)
  }

  // ---- сверка ориентации пар STL+STEP до экспорта ----
  //
  // Пара эмитится вместе, и glb читает STL переводом STEP-ветки (y=+z):
  // STL обязан смотреть в ту же сторону, что STEP (тело в +z, низ у
  // нуля). Проверка ловит старые или чужие файлы, скачанные до смены
  // конвенции: у пары низ ≈ -0.01, верх тела > 0.05. Пропущенная ошибка
  // видна в конце (check_models3d: деталь внутри платы), но лучше
  // сказать об этом до экспорта, с адресом файла.
  const oriBad = []
  for (const [ref, m] of MODELOF) {
    if (!m.stlAbs || !m.stepAbs) continue
    const rng = stlZRange(m.stlAbs)
    if (!rng) continue
    const [szmin, szmax] = rng
    if (szmax <= 0.05 || szmin > 0.06) {
      oriBad.push(`${ref}: STL z=[${szmin.toFixed(2)}, ${szmax.toFixed(2)}]`)
    }
  }
  if (oriBad.length) {
    console.warn(`  ! [STL] ${oriBad.length} STL в паре со STEP не в конвенции — в board.glb детали уйдут внутрь платы:`)
    for (const f of oriBad.slice(0, 6)) console.warn(`         ${f}`)
    console.warn("         пересоберите: python3 tools/part_fetch.py --lcsc <C…> "
      + "(или freecadcmd step2stl.py с NORMALIZE_Z=1)")
  }

  // ---- нативный экспорт через tsci ----
  // ROUTING_ONLY: прогоняем только circuit.json, чтобы проверять
  // авроутинг за секунды, а не ждать все экспорты по минуте каждый.
  const routingOnly = process.env.ROUTING_ONLY === "1"
  const stem = basename(NETLIST).replace(/\.[^.]+$/, "") || "board"
  console.log("\n  [tsci] автотрассировка + экспорт …")
  const jobs = routingOnly
    ? [["json", "circuit.json"]]
    : [
        ["json", "circuit.json"],
        ["kicad_pcb", "board.kicad_pcb"],
        ["kicad_sch", "board.kicad_sch"],
        ["kicad_zip", `${stem}-kicad.zip`],
        ["step", "board.step"],
        ["glb", "board.glb"],
        ["gltf", "board.gltf"],
        ["gerbers", "gerbers.zip"],
        ["readable-netlist", "board.net"],
        ["schematic-pdf", "board_schematic.pdf"],
      ]
  const ok = []
  for (const [fmt, out] of jobs) {
    const t0 = Date.now()
    const good = tsci(["export", TSX, "-f", fmt, "-o", resolve(OUT, out)])
    const secs = ((Date.now() - t0) / 1000).toFixed(1)
    console.log(`      ${good ? "OK  " : "ОШИБКА"} ${fmt.padEnd(16)} ${out.padEnd(26)} ${good ? secs + " с" : ""}`)
    if (good) ok.push(out)
  }

  // ---- итог по автотрассировке ----
  const cjPath = resolve(OUT, "circuit.json")
  if (existsSync(cjPath)) {
    const arr = Object.values(JSON.parse(readFileSync(cjPath, "utf8")))
    const by = {}
    for (const e of arr) by[e.type] = (by[e.type] ?? 0) + 1
    console.log("\n  [circuit.json] " + arr.length + " элементов")
    for (const k of ["pcb_component", "pcb_port", "pcb_smtpad", "pcb_plated_hole",
                     "pcb_trace", "pcb_via", "pcb_copper_pour"]) {
      if (by[k]) console.log(`      ${k}: ${by[k]}`)
    }
    const errs = Object.entries(by).filter(([k]) => k.endsWith("_error"))
    console.log(`  [ошибок] ${errs.length}`)
    for (const [k, n] of errs) console.log(`      ${k}: ${n}`)

    // Диагностика авроутера: первые сообщения об ошибках.
    const msgs = [...new Set(arr.filter((e) => String(e.type).endsWith("_error"))
      .map((e) => e.message).filter(Boolean))]
    for (const m of msgs.slice(0, 6)) console.log(`      ! ${m.slice(0, 190)}`)
  }

  console.log(`\n  Готово: ${ok.length}/${jobs.length} файлов в ${OUT}`)
  if (ok.length < jobs.length) process.exitCode = 1
}

main()