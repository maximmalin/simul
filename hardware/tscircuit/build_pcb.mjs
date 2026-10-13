#!/usr/bin/env node
/**
 * brig-receiver -> код tscircuit из нетлиста KiCad, затем нативный экспорт.
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
import { createRequire } from "node:module"
import { dirname, resolve, relative } from "node:path"

const relPath = (from, to) => relative(from, to)
import { fileURLToPath } from "node:url"
import { spawnSync } from "node:child_process"

const require = createRequire(import.meta.url)
const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, "../..")

const NETLIST = process.env.NETLIST ?? resolve(ROOT, "hardware/kicad/brig_receiver.net")
const OUT = resolve(ROOT, "hardware/tscircuit/out")
const TSX = resolve(OUT, "board.circuit.tsx")
const OFFSETS_FILE = resolve(OUT, "model_offsets.json")
const MODELS = resolve(HERE, "models")
const FP_ROOT = process.env.KICAD_FP_ROOT ?? "/usr/share/kicad/footprints"

// Сети, которые трассируются ПЕРВЫМИ: питание и земля. Остальное идёт
// последним и само обходит уже проложенную медь.
const POWER_NETS = new Set(["GND", "+3V3_DIG", "+5V_ANA", "VBIAS", "VBAT", "+5V", "+3V3"])

// Слои: 4 — двух слоёв для 73 компонентов и ~150 соединений мало.
const BOARD_W = process.env.BOARD_W ?? "170"
const BOARD_H = process.env.BOARD_H ?? "130"

// ═══════════════════════════════════════════════════════ разбор нетлиста KiCad

function parseNetlist(text) {
  const comps = []
  for (const m of text.matchAll(
    /\(comp\s+\(ref\s+"([^"]+)"\)\s*\(value\s+"([^"]*)"\)\s*\(footprint\s+"([^"]*)"\)/g,
  )) comps.push({ ref: m[1], value: m[2], footprint: m[3] })

  const pinFn = new Map()
  for (const m of text.matchAll(
    /\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]+)"\)(?:\s*\(pinfunction\s+"([^"]*)"\))?/g,
  )) pinFn.set(`${m[1]}.${m[2]}`, m[3] ?? "")

  const nets = []
  // Блок сети закрывается ")))" в конце строки последнего узла, поэтому
  // искать "\n  )" бесполезно. Границы берём по позициям следующего
  // заголовка (net ...) — это устойчиво к любому отступу.
  const heads = [...text.matchAll(/\(net\s+\(code\s+"(\d+)"\)\s+\(name\s+"([^"]*)"/g)]
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
// Сдвиги 3D-моделей: "<ref>" -> {dx,dy}. Заполняется из model_offsets.json.
let OFFSETS = new Map()
// Кого в текущем проходе пришлось сдвинуть (для отчёта).
let shiftedRefs = []
// Размещение компонентов: ref -> {x,y}. Заполняется в emitTsx, нужно
//computeOffsets, чтобы перевести pcb_component.center в локальные
// координаты корпуса.
let PLACE = new Map()
// ref -> {ident, abs, name}
let MODELOF = new Map()

const j = (v) => JSON.stringify(String(v))
const num = (v) => Number(v)

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
 * STL-модель для корпуса.
 *
 * Путь берётся из строки (model ...) файла .kicad_mod. Но у части
 * корпусов её нет вовсе — например TestPoint_Pad_D1.0mm, у которого
 * строка модели просто опущена. Тогда tscircuit подставляет в
 * circuit.json ссылку на kicad-mod-cache.tscircuit.com, и сайт
 * начинает ходить в сеть, чего быть не должно.
 *
 * Поэтому при отсутствии строки модели проверяется соглашение
 * packages3d: <Lib>.3dshapes/<Name>.step. Проверка идёт по факту
 * наличия файла, а не по списку корпусов, так что переносится на
 * любую схему.
 */
function stlModelFor(libId, name) {
  const mod = resolve(FP_ROOT, `${libId}.pretty`, `${name}.kicad_mod`)
  if (!existsSync(mod)) return null
  const rel = readFileSync(mod, "utf8")
    .match(/KICAD9_3DMODEL_DIR}\/[^"]*\.step/)?.[0]
    ?.replace("KICAD9_3DMODEL_DIR}/", "")
  const candidates = rel
    ? [rel]
    : [`${libId}.3dshapes/${name}.step`]
  for (const c of candidates) {
    const stl = resolve(MODELS, c.replace(/\.step$/i, ".stl"))
    if (existsSync(stl)) return stl
  }
  return null
}

/** Идентификатор импорта для пути к модели. */
function importIdent(absPath) {
  return "m" + relPath(MODELS, absPath).replace(/[^A-Za-z0-9]/g, "_")
}

/**
 * Габаритный параллелепипед STL: {xmin,xmax,ymin,ymax,zmin,zmax}.
 *
 * Читается напрямую, без сторонних библиотек: формат STL-ASCII и
 * STL-binary тут прост, а тяновать за собой загрузчик ради габаритов
 * незачем. Читаются только вершины треугольников (каждая 4-я пара
 * float32 в записи), нормали игнорируются.
 */
function stlBBox(path) {
  let buf
  try {
    buf = readFileSync(path)
  } catch {
    return null
  }
  const add = (x, y, z) => {
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return
    if (b.xmin === null || x < b.xmin) b.xmin = x
    if (b.xmax === null || x > b.xmax) b.xmax = x
    if (b.ymin === null || y < b.ymin) b.ymin = y
    if (b.ymax === null || y > b.ymax) b.ymax = y
    if (b.zmin === null || z < b.zmin) b.zmin = z
    if (b.zmax === null || z > b.zmax) b.zmax = z
  }
  const b = { xmin: null, xmax: null, ymin: null, ymax: null, zmin: null, zmax: null }

  // ASCII- STL начинается со слова "solid"
  if (buf.subarray(0, 5).toString("ascii") === "solid") {
    const txt = buf.toString("ascii")
    for (const m of txt.matchAll(/vertex\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)/g)) {
      add(+m[1], +m[2], +m[3])
    }
    return b.xmin === null ? null : b
  }

  // binary- STL: 80 байт заголовка, затем uint32 со счётчиком
  // треугольников, далее по 50 байт на треугольник: 12 float32
  // (нормаль + 3 вершины) + uint16 атрибута.
  const n = buf.readUInt32LE(80)
  let off = 84
  for (let i = 0; i < n; i++) {
    if (off + 48 > buf.length) break
    for (const k of [3, 6, 9]) {
      const o = off + k * 4
      add(buf.readFloatLE(o), buf.readFloatLE(o + 4), buf.readFloatLE(o + 8))
    }
    off += 50
  }
  return b.xmin === null ? null : b
}

/**
 * Центр выводов корпуса из .kicad_mod.
 *
 * Именно выводы, а не courtyard: courtyard у KiCad бывает смещён
 * относительно начала координат (у PinHeader_1x06 он центрирован в
 * y=6.35, тогда как начало координат — на первом выводе).
 */
function padCenter(libId, name) {
  const mod = resolve(FP_ROOT, `${libId}.pretty`, `${name}.kicad_mod`)
  if (!existsSync(mod)) return null
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
  const mod = resolve(FP_ROOT, `${libId}.pretty`, `${name}.kicad_mod`)
  if (!existsSync(mod)) return { w: 5, h: 5 }
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
  if (!pts.length) return { w: 5, h: 5 }
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1])
  return { w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys) }
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
  PLACE = place
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
    place.set(c.ref, { x: rowX + s.w / 2, y: -(rowY + s.h / 2) })
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
    minX = Math.min(minX, p.x - s.w / 2)
    maxX = Math.max(maxX, p.x + s.w / 2)
    minY = Math.min(minY, p.y - s.h / 2)
    maxY = Math.max(maxY, p.y + s.h / 2)
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
  L.push("// СГЕНЕРИРОВАНО автоматически из hardware/kicad/brig_receiver.net")
  L.push("// Не редактировать вручную: источник истины — принципиальная схема KiCad.")
  L.push("// Обработку (автотрассировку, 3D, экспорт) выполняет официальный tsci.")
  L.push("")
  const { place, width, height } = computePlacement()
  L.push(`const BOARD_W = ${width}`)
  L.push(`const BOARD_H = ${height}`)
  L.push("")

  // ---- 3D-модели: импортируем каждый STL один раз ----
  // Смещение модели относительно выводов считается автоматически для
  // каждой детали, поэтому перенос на другую схему ничего не потребует.
  const modelOf = new Map()
  MODELOF = modelOf
  const imported = new Map()  // abs -> ident  // abs -> {dx,dy}|null, считается один раз
  for (const c of COMPONENTS) {
    const [lib, name] = c.footprint.split(":")
    const abs = stlModelFor(lib, name)
    if (!abs) continue
    let ident = imported.get(abs)
    if (!ident) {
      ident = importIdent(abs)
      imported.set(abs, ident)
      L.push(`import ${ident} from ${j(relPath(OUT, abs))}`)
    }
    modelOf.set(c.ref, { ident, abs, name })
  }
  L.push("")
  shiftedRefs.length = 0

  L.push("export default () => (")
  L.push(`  <board width={\`\${BOARD_W}mm\`} height={\`\${BOARD_H}mm\`} layers={4} minTraceWidth="0.15mm" nominalTraceWidth="0.25mm">`)

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
    const fp = `kicad:${c.footprint.replace(":", "/")}`
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
      // Сдвиг модели относительно центра компонента.
      //
      // tscircuit ставит узел модели в cad_component.position и НЕ
      // центрирует её по габариту, поэтому модель встаёт ровно там, где
      // записаны её координаты. У моделей из packages3d начало координат
      // совпадает с началом корпуса, и сдвиг не нужен. У моделей из
      // каталога LCSC/EasyEDA начало произвольное, и деталь уезжает
      // (проверено: ESP32 на 2.99 мм, Crystal_AT310 на 6.03 мм).
      //
      // Величина берётся из model_offsets.json, который считается по
      // данным circuit.json (см. computeOffsets): это
      //   pcb_component.center − (pcbX, pcbY) − центр габарита STL,
      // то есть ровно то, что нужно, без предположений о том, как
      // tscircuit вычисляет центр корпуса.
      //
      // z обязателен: в схеме пропов positionOffset требует все три оси,
      // и без z объект не проходит валидацию — tscircuit падает с
      // "Could not create cadmodel" и компонент пропадает из платы.
      const off = OFFSETS.get(c.ref)
      const offStr = off
        ? ` positionOffset={{ x: ${off.dx}, y: ${off.dy}, z: 0 }}`
        : ""
      if (off) shiftedRefs.push(c.ref)
      L.push(`      cadModel={<cadmodel modelUrl={${m.ident}}${offStr} />}`)
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
    if (net.name === "GND") flags.push("isGroundNet")
    else if (POWER_NETS.has(net.name)) flags.push("isPowerNet")
    L.push(`    <net name=${j(tscNetName(net.name))}${flags.length ? " " + flags.join(" ") : ""} />`)
  }

  // ---- трассы: каждый вывод -> своя сеть ----
  // Для сетей, у которых объявлена медная заливка, трассы НЕ создаются:
  // заливка соединяет их площадки сама, а трассы лишь добавляют авроутеру
  // работу. Один GND давал 46 трасс, +3V3_DIG — ещё 15; вместе это
  // почти половина задачи и причина исчерпания итераций.
  const poured = new Set(["GND", "P3V3_DIG", "P5V_ANA", "VBIAS"])
  let i = 0
  for (const net of NETS) {
    if (poured.has(tscNetName(net.name))) continue
    for (const nd of net.nodes) {
      i++
      L.push(`    <trace name=${j(`${tscNetName(net.name)}_${i}`)} from=${j(`${nd.ref}.pin${nd.pin}`)} to=${j("net." + tscNetName(net.name))} />`)
    }
  }

  // ---- заливки ----
  // GND разводится заливкой, а не 45 трассами: это и электрически
  // правильнее (низкое сопротивление), и снимает нагрузку с авроутера.
  if (netNames.includes("GND")) {
    // Заливка GND: нижний слой — обратная плоскость, inner1 — выделенная
    // плоскость питания, к которой авроутер выводит силовые соединения
    // через фазу fanout (см. fanoutPourNetMap выше).
    L.push(`    <copperpour connectsTo=${j("net.GND")} layer="bottom" />`)
    L.push(`    <copperpour connectsTo=${j("net.GND")} layer="inner1" />`)
  }
  if (netNames.includes("P3V3_DIG")) {
    L.push(`    <copperpour connectsTo=${j("net.P3V3_DIG")} layer="inner2" />`)
  }
  // Остальные силовые сети — на верхний слой, разводятся трассами
  // в фазе 0 (см. routingPhaseIndex выше).
  for (const n of ["+3V3_DIG", "+5V_ANA", "VBIAS"].map(tscNetName)) {
    if (netNames.includes(n)) {
      L.push(`    <copperpour connectsTo=${j("net." + tscNetName(n))} layer="top" />`)
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
  const fatal = /SyntaxError|Cannot find module|ERR_MODULE_NOT_FOUND/.test(out)
  if (r.status !== 0 || fatal) {
    console.error(`  ! tsci ${args.join(" ")} -> ${r.status}`)
    console.error(out.split("\n").slice(0, 12).map((l) => "      " + l).join("\n"))
    return false
  }
  return true
}

// ═══════════════════════════════════════════════════════ main

/**
 * Чтение кэша сдвигов моделей.
 */
function loadOffsets() {
  if (!existsSync(OFFSETS_FILE)) return new Map()
  try {
    const j = JSON.parse(readFileSync(OFFSETS_FILE, "utf8"))
    return new Map(Object.entries(j.offsets ?? {}))
  } catch {
    return new Map()
  }
}

/**
 * Расчёт сдвигов 3D-моделей по фактическому circuit.json.
 *
 * tscircuit не центрирует модель по габариту: узел встаёт в
 * cad_component.position буквально. Чтобы деталь села по центру
 * компонента, нужно
 *
 *   positionOffset = (pcb_component.center − (pcbX, pcbY)) − центр(STL)
 *
 * где первая скобка — начало координат корпуса в его собственной
 * системе (как его понял tscircuit), вторая — центр габарита модели.
 *
 * Почему не по падам. Центр по падам отличается от того, что использует
 * tscircuit: у PinHeader_1x08 пады дают +8.89 мм, а tscircuit ставит
 * начало корпуса на −8.89 мм, то есть расхождение 17.78 мм. Проверено
 * на 9 корпусах: по circuit.json сдвиг сходится до 0.00 мм, по падам —
 * нет. Поэтому источник истины — экспорт tscircuit, а не разбор
 * посадочного места.
 *
 * Результат кэшируется: смещение зависит только от корпуса, а не от
 * позиции, поэтому второй проход уже не нужен.
 */
function computeOffsets(place, modelOf) {
  const cj = resolve(OUT, "circuit.json")
  if (!existsSync(cj)) {
    console.error("  ! нет circuit.json — сдвиги посчитать не по чему")
    return null
  }
  const arr = Object.values(JSON.parse(readFileSync(cj, "utf8")))
  const nameOf = new Map(
    arr.filter((e) => e.type === "source_component").map((e) => [e.source_component_id, e.name]),
  )
  const centerOf = new Map()
  for (const e of arr) {
    if (e.type !== "pcb_component") continue
    centerOf.set(nameOf.get(e.source_component_id), e.center)
  }

  const out = {}
  const rows = []
  for (const [ref, m] of modelOf) {
    const b = stlBBox(m.abs)
    const c = centerOf.get(ref)
    const p = place.get(ref)
    if (!b || !c || !p) continue
    const lx = c.x - p.x
    const ly = c.y - p.y
    const dx = +(lx - (b.xmin + b.xmax) / 2).toFixed(4)
    const dy = +(ly - (b.ymin + b.ymax) / 2).toFixed(4)
    if (Math.abs(dx) < 0.01 && Math.abs(dy) < 0.01) continue
    out[ref] = { dx, dy }
    rows.push(`      ${ref.padEnd(6)} d=(${dx.toFixed(3)}, ${dy.toFixed(3)})  ${m.name}`)
  }
  if (!rows.length) {
    console.log("  [сдвиги] ни одной модели не требует сдвига")
    return out
  }
  console.log(`  [сдвиги] сдвинуть моделей: ${rows.length}`)
  for (const r of rows) console.log(r)
  return out
}

function main() {
  mkdirSync(OUT, { recursive: true })

  console.log("=".repeat(74))
  console.log("BRIG RECEIVER -> tscircuit (эмиттер TSX + нативный экспорт tsci)")
  console.log("=".repeat(74))
  console.log(`\n  [вход] ${NETLIST}`)

  const { comps, nets, pinFn } = parseNetlist(readFileSync(NETLIST, "utf8"))
  COMPONENTS = comps.filter((c) => !c.ref.startsWith("#"))
  NETS = nets
  PINFN = pinFn

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

  // ---- проверка корпусов локально, до эмита ----
  const missingFp = []
  for (const c of COMPONENTS) {
    const [lib, name] = c.footprint.split(":")
    const p = resolve("/usr/share/kicad/footprints", `${lib}.pretty`, `${name}.kicad_mod`)
    if (!existsSync(p)) missingFp.push(`${c.ref}: ${c.footprint}`)
  }
  if (missingFp.length) {
    console.error(`\n  ОШИБКА: нет ${missingFp.length} корпусов локально:`)
    for (const m of missingFp.slice(0, 20)) console.error("      " + m)
    process.exit(1)
  }
  console.log(`  [корпуса] все ${new Set(COMPONENTS.map((c) => c.footprint)).size} уникальных найдены локально`)

  // ---- сдвиги 3D-моделей из предыдущего прохода ----
  OFFSETS = loadOffsets()
  console.log(
    `  [сдвиги] ${OFFSETS.size ? `${OFFSETS.size} моделей со сдвигом из ${relPath(ROOT, OFFSETS_FILE)}` : "нет файла со сдвигами — модели встанут как есть"}`,
  )

  // ---- эмитт TSX ----
  writeFileSync(TSX, emitTsx())
  // Трассы создаются не для всех сетей: у сетей с медной заливкой
  // площадки соединяет заливка. Считаем по факту.
  const pouredNames = new Set(["GND", "P3V3_DIG", "P5V_ANA", "VBIAS"])
  const nTraces = NETS
    .filter((n) => !pouredNames.has(tscNetName(n.name)))
    .reduce((a, n) => a + n.nodes.length, 0)
  console.log(`  [эмитт] ${TSX}`)
  console.log(`          компонентов ${COMPONENTS.length}, трасс ${nTraces}`)
  console.log(`          моделей со сдвигом: ${shiftedRefs.length}`)

  // ---- сдвиги моделей: считаем после circuit.json, переэмитим и повторяем ----
  //
  // Первый проход нужен только ради circuit.json: из него берётся то
  // положение, которое tscircuit приписал корпусу. Смещение зависит от
  // корпуса, а не от позиции, поэтому результат кэшируется в
  // model_offsets.json и повторный расчёт не нужен.
  const wantOffsets = process.env.COMPUTE_OFFSETS === "1"
  if (wantOffsets) {
    const off = computeOffsets(PLACE, MODELOF)
    if (off) {
      writeFileSync(OFFSETS_FILE, JSON.stringify({ offsets: off }, null, 1) + "\n")
      OFFSETS = new Map(Object.entries(off))
      console.log(`  [сдвиги] записано ${OUT ? "" : ""}${relPath(ROOT, OFFSETS_FILE)} (${Object.keys(off).length})`)
      if (shiftedRefs.length) {
        writeFileSync(TSX, emitTsx())
        console.log(`  [эмитт] пересобран со сдвигами: ${shiftedRefs.join(", ")}`)
      }
    }
  }

  // ---- нативный экспорт через tsci ----
  // ROUTING_ONLY: прогоняем только circuit.json, чтобы проверять
  // авроутинг за секунды, а не ждать все 8 экспортов по минуте каждый.
  const routingOnly = process.env.ROUTING_ONLY === "1"
  console.log("\n  [tsci] автотрассировка + экспорт …")
  const jobs = routingOnly
    ? [["json", "circuit.json"]]
    : [
        ["json", "circuit.json"],
        ["kicad_pcb", "board.kicad_pcb"],
        ["kicad_zip", "brig-receiver-kicad.zip"],
        ["step", "board.step"],
        ["glb", "board.glb"],
        ["gltf", "board.gltf"],
        ["gerbers", "gerbers.zip"],
        ["readable-netlist", "board.net"],
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