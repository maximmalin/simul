#!/usr/bin/env node
/**
 * convert-kicad-to-circuit-json.js
 * Конвертирует KiCad schematic в Circuit JSON через kicad-to-circuit-json.
 * 
 * Использование:
 *   node scripts/convert-kicad-to-circuit-json.js [kicad_sch] [output_file]
 * 
 * По умолчанию:
 *   kicad_sch = kicad/block0/block0_5v_bus.kicad_sch
 *   output_file = circuit.json (в корне проекта)
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");

const DEFAULT_KICAD_SCH = path.join(PROJECT_ROOT, "kicad", "block0", "block0_5v_bus.kicad_sch");
const DEFAULT_OUTPUT = path.join(PROJECT_ROOT, "circuit.json");

const kicadSch = process.argv[2] || process.env.KICAD_SCH || DEFAULT_KICAD_SCH;
const outputPath = process.argv[3] || process.env.OUTPUT_CIRCUIT_JSON || DEFAULT_OUTPUT;

function log(msg) { console.log(`[conv] ${msg}`); }
function err(msg) { console.error(`[conv:ERR] ${msg}`); }

if (!fs.existsSync(kicadSch)) {
  err(`KiCad schematic не найден: ${kicadSch}`);
  process.exit(1);
}

log(`Input:  ${kicadSch}`);
log(`Output: ${outputPath}`);

const kicadContent = fs.readFileSync(kicadSch, "utf8");

try {
  const { KicadToCircuitJsonConverter } = await import("kicad-to-circuit-json");
  const converter = new KicadToCircuitJsonConverter();
  converter.addFile(path.basename(kicadSch), kicadContent);
  log("Запуск конвертации...");
  converter.runUntilFinished();
  const circuitJson = converter.getOutput();

  fs.writeFileSync(outputPath, JSON.stringify(circuitJson, null, 2), "utf8");
  log(`✓ Circuit JSON сохранён: ${outputPath} (${(fs.statSync(outputPath).size / 1024).toFixed(1)} KB)`);

  // getOutput() отдаёт МАССИВ элементов с полем "type", а не объект
  // с полями source_components/pcb_components. Считаем по типам.
  const elems = Array.isArray(circuitJson) ? circuitJson : [circuitJson];
  const byType = {};
  for (const e of elems) byType[e.type ?? "?"] = (byType[e.type ?? "?"] ?? 0) + 1;

  log("Структура (элементов по типам):");
  for (const [t, n] of Object.entries(byType).sort((a, b) => b[1] - a[1])) {
    log(`  ${t}: ${n}`);
  }

  const srcComps = elems.filter((e) => e.type === "source_component");
  const pcbComps = elems.filter((e) => e.type === "pcb_component");
  log(`\nИТОГО элементов: ${elems.length}`);
  log(`  source_components: ${srcComps.length}`);
  log(`  pcb_components   : ${pcbComps.length}`);

  const nets = elems.filter((e) => String(e.type ?? "").startsWith("source_net"));
  log(`  source_net*      : ${nets.length}`);

  if (srcComps.length > 0) {
    log("\nКомпоненты:");
    for (const comp of srcComps) {
      const ref = comp.ref || comp.name || comp.id || "?";
      log(`  - ${ref}: ${comp.name ?? "?"} (ftype: ${comp.ftype || "?"})`);
      if (comp.manufacturer_part_number)
        log(`    MPN: ${comp.manufacturer_part_number}`);
      if (comp.supplier_part_numbers)
        log(`    suppliers: ${JSON.stringify(comp.supplier_part_numbers)}`);
    }
  }

  log("\nГотово! Запустить скачивание:");
  log(`  node scripts/download-datasheets.js ${outputPath} datasheets`);

  if (process.env.SKIP_DOWNLOAD !== "1") {
    log("\nАвтоматический запуск скачивания...");
    try {
      const { default: downloadModule } = await import("./download-datasheets.js");
      const { run } = downloadModule;
      const outDir = path.join(PROJECT_ROOT, "datasheets");
      const result = await run(outputPath, outDir);
      log(`Результат: ${result.success} скачано, ${result.failed} ошибок, ${result.skipped} пропущено`);
    } catch (e) {
      err(`Ошибка скачивания: ${e.message}`);
    }
  }
} catch (e) {
  err(`Ошибка: ${e.message}`);
  console.error(e);
  process.exit(1);
}
