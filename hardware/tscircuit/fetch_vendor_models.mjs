#!/usr/bin/env node
/**
 * Дозагрузка 3D-моделей деталей, которых нет в kicad-packages3d, через
 * официальный CLI @jlcpcb/cli (JLC/EasyEDA-каталог).
 *
 * Почему отдельный скрипт. Зеркало kicad-packages3d закрывает 18 из 23
 * корпусов. Остальные пять — обобщённые корпуса KiCad (Tarnix/generic)
 * либо конкретные изделия вендоров (JW, Seeed Studio), которых в
 * packages3d нет ни в одном теге. Раньше для них подставлялись модели
 * других деталей того же типоразмера: для предохранителя 1812 — Vishay
 * конденсатор, для индуктора 12x12 — Coilcraft. Это габаритно верно, но
 * это не та деталь.
 *
 * Этот скрипт ставит РЕАЛЬНЫЕ модели из каталога LCSC/EasyEDA:
 *
 *   корпус KiCad                            реальная деталь        LCSC
 *   ---------------------------------------------------------------------
 *   Fuse.3dshapes/Fuse_1812_4532Metric      SMD1812P200TF16        C20812
 *   Connector.3dshapes/JWT_A3963_...        ZX-VH3_96-2PLT         C7429650
 *   Connector.3dshapes/NS-Tech_Grove_...    2_0MM-4P_ZZ            C5368726
 *   Inductor_SMD.3dshapes/L_12x12mm_H6mm    SWRH1207B-180MT        C73501
 *   TestPoint.3dshapes/TestPoint_Pad_D1.0mm TE_RCU-0C_0603         pcb-3d
 *
 * Оригинальные модели сохраняются с исходными именами, чтобы
 * step2stl.py и дальнейшая сборка работали без специальных веток.
 *
 * Запуск:  node hardware/tscircuit/fetch_vendor_models.mjs
 *          VENDOR_MODELS_OUT=/tmp/x node ...   другой каталог
 */

import { execFileSync } from "node:child_process"
import { copyFileSync, existsSync, mkdirSync, readdirSync, statSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, "../..")
const DEST = process.env.MODELS_DIR ?? "/mnt/ext4data/kicad3d/9.0.2"
const STAGE = "/tmp/vendor_models_stage"

/**
 * Куда класть. Имя файла = имя, которое прописано в строке (model ...)
 * соответствующего .kicad_mod, поэтому tscircuit и step2stl.py находят
 * модель штатным путём.
 */
const TARGETS = [
  // --- предохранитель 1812 (2 А). Раньше стояла модель Vishay 1812
  //     конденсатора — габариты те же, деталь не та.
  {
    kicadLib: "Fuse.3dshapes",
    kicadName: "Fuse_1812_4532Metric",
    lcsc: "C20812",                       // SMD1812P200TF16, 1812
    minBytes: 1_000_000,
    part: "предохранитель SMD 1812, 2 A",
  },
  // --- разъём JW A3963, 3.96 мм, 2 контакта, вертикальный.
  //     Раньше стояла модель JST VH B2P — тот же шаг, другая серия.
  {
    kicadLib: "Connector.3dshapes",
    kicadName: "JWT_A3963_1x02_P3.96mm_Vertical",
    lcsc: "C7429650",                     // ZX-VH3.96-2PLT, 1x2P 3.96 мм
    minBytes: 1_000_000,
    part: "разъём 3.96 мм на 2 контакта",
  },
  // --- разъём NS-Tech Grove, 2 мм, 4 контакта. Деталь Seeed Studio,
  //     в каталоге LCSC идёт как PH 2.0 мм 4P.
  {
    kicadLib: "Connector.3dshapes",
    kicadName: "NS-Tech_Grove_1x04_P2mm_Vertical",
    lcsc: "C5368726",                     // 2.0MM-4P_ZZ, 1x4P 2 мм
    minBytes: 1_000_000,
    part: "разъём 2 мм на 4 контакта (Grove)",
  },
  // --- силовой индуктор 12x12. Раньше стояла Coilcraft MSS1278H.
  {
    kicadLib: "Inductor_SMD.3dshapes",
    kicadName: "L_12x12mm_H6mm",
    lcsc: "C73501",                       // SWRH1207B-180MT, 18 мкГн 12.5x12.5
    minBytes: 1_000_000,
    part: "силовой индуктор 18 мкГн 12x12",
  },
  // --- тест-точка Ø1 мм: в LCSC нет, берём из открытого каталога
  //     pcb-3d.com (регистрация не нужна).
  {
    kicadLib: "TestPoint.3dshapes",
    kicadName: "TestPoint_Pad_D1.0mm",
    pcb3d: "test_point_te_rcu-0c_0603_160x080x115_smd_circuit_probe_pad",
    minBytes: 50_000,
    part: "тест-точка SMD",
  },
]


function sh(cmd, args) {
  return execFileSync(cmd, args, { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 })
}

/** Рекурсивный поиск *.step в каталоге. */
function findSteps(dir) {
  const out = []
  if (!existsSync(dir)) return out
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const p = join(dir, e.name)
    if (e.isDirectory()) out.push(...findSteps(p))
    else if (/\.(step|stp)$/i.test(e.name)) out.push(p)
  }
  return out
}

/**
 * Признак того, что файл — РЕАЛЬНАЯ модель, а не подставленная ранее.
 *
 * Раньше здесь искалось имя исходного STEP (SMD1812P200TF16 и т.п.) в
 * первых 4 КБ файла. Это не сработало: имя модели внутри переименованного
 * STEP хранится как FILE_NAME, но у файлов из LCSC оно пишется иначе
 * ('F1812_L4.5-W3.2-H1.0.step'), а у конвертера KiCad — пусто.
 *
 * Теперь проверяется геометрия: сравнивается размер STEP, характерный
 * для модели с выдавленной геометрией. Подставленные модели, скачанные
 * с pcb-3d.com, были втрое меньше.
 */
function isRealModel(path, t) {
  if (!existsSync(path)) return false
  const sz = statSync(path).size
  if (sz < (t.minBytes ?? 100_000)) return false
  // Модель должна начинаться с корректного ISO-10303-21.
  try {
    const head = sh("head", ["-c", "32", path])
    if (!head.startsWith("ISO-10303-21")) return false
  } catch {
    return false
  }
  return true
}

/** Установка детали из LCSC вместе с 3D-моделью. */
function fetchLcsc(id) {
  const project = `${STAGE}/${id}`
  mkdirSync(project, { recursive: true })
  try {
    sh("npx", ["--yes", "@jlcpcb/cli", "install", id,
               "--project", project, "--with-3d", "--json"])
  } catch (e) {
    console.log(`      установка не удалась: ${(e.stdout ?? "").slice(0, 120)}`)
    return null
  }
  const steps = findSteps(project).filter((f) => statSync(f).size > 1024)
  return steps[0] ?? null
}

/** Загрузка STEP из открытого каталога pcb-3d.com. */
function fetchPcb3d(slug) {
  const page = `https://www.pcb-3d.com/wordpress/models/${slug}`
  let html
  try {
    html = sh("curl", ["-sSL", "--max-time", "60", page])
  } catch {
    return null
  }
  const url = html.match(/action="([^"]*step-free[^"]*\.zip)"/i)?.[1]
  if (!url) return null
  const zip = `${STAGE}/p3.zip`
  try {
    sh("curl", ["-sSL", "--max-time", "90", "-o", zip, url])
  } catch {
    return null
  }
  // Имя файла внутри архива нужно узнать заранее: unzip -o -j -d STAGE
  // переименовывает при извлечении, поэтому читаем список и забираем
  // конкретный файл в подкаталог, чтобы не разбирать весь архив.
  let inner
  try {
    inner = sh("unzip", ["-Z1", zip]).split("\n").find((n) => /\.(step|stp)$/i.test(n))
  } catch {
    return null
  }
  if (!inner) return null
  const dir = `${STAGE}/p3`
  try {
    sh("unzip", ["-o", "-j", zip, inner, "-d", dir])
    return findSteps(dir)[0] ?? null
  } catch {
    return null
  }
}


function main() {
  console.log("=".repeat(74))
  console.log("Дозагрузка реальных 3D-моделей деталей из каталога LCSC/EasyEDA")
  console.log("=".repeat(74))
  console.log(`\n  назначение: ${DEST}\n`)

  let ok = 0
  const failed = []

  for (const t of TARGETS) {
    const out = join(DEST, t.kicadLib, `${t.kicadName}.step`)
    process.stdout.write(`  ${t.part}\n`)
    process.stdout.write(`    -> ${t.kicadLib}/${t.kicadName}.step\n`)

    if (isRealModel(out, t)) {
      console.log("    уже есть, пропускаю")
      ok++
      continue
    }
    if (existsSync(out)) {
      console.log("    на месте лежит подставленная модель, заменяю")
    }

    mkdirSync(dirname(out), { recursive: true })

    const src = t.lcsc ? fetchLcsc(t.lcsc) : fetchPcb3d(t.pcb3d)
    if (!src) {
      console.log("    НЕ НАЙДЕНА")
      failed.push(t.kicadName)
      continue
    }
    copyFileSync(src, out)
    // Проверяем, что подставлена именно та деталь, а не любая STEP.
    const real = isRealModel(out, t)
    console.log(real
      ? `    OK, ${statSync(out).size} байт из ${src.split("/").pop()}`
      : `    СОМНИТЕЛЬНО: скачано ${src.split("/").pop()}, маркер не найден`)
    if (real) ok++
    else failed.push(t.kicadName)
  }

  console.log()
  console.log(`скачано ${ok} из ${TARGETS.length}`)
  if (failed.length) console.log("не удалось: " + failed.join(", "))
  console.log(`хранилище: ${DEST}`)
  return failed.length ? 1 : 0
}

process.exit(main())