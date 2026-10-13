#!/usr/bin/env node
/**
 * post-build-datasheets.js
 * Post-build hook: читает graphify-out/graph.json и запускает скачивание датащитов.
 * 
 * Использование в package.json:
 *   "scripts": {
 *     "postbuild": "node scripts/post-build-datasheets.js"
 *   }
 * 
 * Альтернативно: запуск напрямую
 *   node scripts/post-build-datasheets.js
 *   node scripts/post-build-datasheets.js circuit.json datasheets
 */

import { run } from "./download-datasheets.js";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");

const DEFAULT_GRAPH = path.join(PROJECT_ROOT, "graphify-out", "graph.json");
const DEFAULT_OUT_DIR = path.join(PROJECT_ROOT, "datasheets");

const circuitPath = process.argv[2] || DEFAULT_GRAPH;
const outputDir = process.argv[3] || DEFAULT_OUT_DIR;

if (!fs.existsSync(circuitPath)) {
  console.error(`[hook] Граф не найден: ${circuitPath}`);
  console.error(`       Запустите сначала: tsci build`);
  process.exit(1);
}

console.log(`[hook] Запуск скачивания датащитов после сборки`);
console.log(`[hook] Граф: ${circuitPath}`);
console.log(`[hook] Папка: ${outputDir}`);
console.log("");

const result = await run(circuitPath, outputDir);
console.log(`[hook] Разбор: ${result.success} скачано, ${result.failed} ошибок, ${result.skipped} пропущено`);

process.exit(result.failed > 0 ? 1 : 0);
