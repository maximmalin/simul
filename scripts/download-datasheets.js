#!/usr/bin/env node
/**
 * download-datasheets.js
 * Автоматическое скачивание датащитов для компонентов из circuit.json.
 * 
 * Пайплайн:
 *   1. Читает circuit.json (результат tsci build или kicad-to-circuit-json)
 *   2. Извлекает компоненты из source_components и pcb_components
 *   3. Для каждого компонента собирает идентификаторы (MPN, JLCPCB, LCSC, ref, name)
 *   4. Поиск датащитов по приоритету:
 *      a) datasheetUrl из circuit.json
 *      b) JLCPCB OpenAPI (open.jlcpcb.com/api/part/search)
 *      c) LCSC API (board.lcsc.com/v1/device/query)
 *      d) Octopart API (если задан OCTOPART_API_KEY)
 *   5. Скачивает PDFs в папку datasheets/
 * 
 * Запуск:
 *   node scripts/download-datasheets.js [circuit.json] [output-dir]
 * 
 * Интеграция в package.json:
 *   "scripts": {
 *     "datasheets": "node scripts/download-datasheets.js",
 *     "postbuild": "node scripts/download-datasheets.js"
 *   }
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import https from "https";
import http from "http";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = path.resolve(__dirname, "..");

const DEFAULT_CIRCUIT = path.join(PROJECT_ROOT, "circuit.json");
const DEFAULT_OUT_DIR = path.join(PROJECT_ROOT, "datasheets");

const CONCURRENCY = 4;
const DOWNLOAD_TIMEOUT = 60_000;
const FETCH_TIMEOUT = 15_000;
const MAX_RETRIES = 2;

const JLCPCB_SEARCH_URL = "https://open.jlcpcb.com/api/part/search";
const LCSC_SEARCH_URL = "https://board.lcsc.com/v1/device/query";

const OCTOPART_API_KEY = process.env.OCTOPART_API_KEY || "";
const OCTOPART_SEARCH_URL = "https://api.octopart.com/v4/parts/search";

const log = (...args) => console.log(`[dsheet] ${args.join(" ")}`);
const errLog = (...args) => console.error(`[dsheet:ERR] ${args.join(" ")}`);

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function sanitize(str) {
  return str.replace(/[<>:"/\\|?*]/g, "_").slice(0, 200);
}

function httpGet(url, timeout = FETCH_TIMEOUT) {
  return new Promise((resolve, reject) => {
    const proto = url.startsWith("https") ? https : http;
    const req = proto.get(url, { timeout }, (res) => {
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode} для ${url}`));
        return;
      }
      let body = "";
      res.on("data", (c) => { body += c; });
      res.on("end", () => {
        try {
          resolve(JSON.parse(body));
        } catch {
          reject(new Error(`Невалидный JSON от ${url}`));
        }
      });
    });
    req.on("error", reject);
    req.on("timeout", () => { req.destroy(); reject(new Error("Таймаут")); });
  });
}

async function searchJlcpcb(keyword) {
  if (!keyword) return null;
  try {
    const url = `${JLCPCB_SEARCH_URL}?keyword=${encodeURIComponent(keyword)}&limit=5`;
    log(`JLCPCB поиск: "${keyword}"`);
    const data = await httpGet(url, 20_000);
    if (data?.results?.length > 0) {
      const p = data.results[0];
      return {
        source: "jlcpcb_api",
        datasheetUrl: p.datasheet_url || p.specs?.datasheet || null,
        partNumber: p.part_number || p.mpn || null,
        manufacturer: p.manufacturer || null,
        description: p.description || null,
      };
    }
  } catch (e) {
    errLog(`JLCPCB поиск не удался для "${keyword}": ${e.message}`);
  }
  return null;
}

async function searchLcsc(keyword) {
  if (!keyword) return null;
  try {
    const url = `${LCSC_SEARCH_URL}?keyword=${encodeURIComponent(keyword)}&page_size=5`;
    log(`LCSC поиск: "${keyword}"`);
    const data = await httpGet(url, 20_000);
    if (data?.results?.length > 0) {
      const p = data.results[0];
      return {
        source: "lcsc_api",
        datasheetUrl: p.datasheet_url || null,
        lcscNum: p.device_num || null,
        manufacturer: p.manufacturer || null,
        description: p.description || null,
      };
    }
  } catch (e) {
    errLog(`LCSC поиск не удался для "${keyword}": ${e.message}`);
  }
  return null;
}

async function searchOctopart(keyword, mpn = "") {
  if (!OCTOPART_API_KEY) {
    errLog("Octopart API ключ не задан (OCTOPART_API_KEY)");
    return null;
  }
  try {
    const params = new URLSearchParams({
      apikey: OCTOPART_API_KEY,
      keyword,
      limit: "3",
    });
    if (mpn) params.append("mpn", mpn);
    const url = `${OCTOPART_SEARCH_URL}?${params}`;
    log(`Octopart поиск: "${keyword}"${mpn ? ` (MPN: ${mpn})` : ""}`);
    const data = await httpGet(url, 20_000);
    if (data?.parts?.length > 0) {
      const p = data.parts[0];
      return {
        source: "octopart_api",
        datasheetUrl: p.datasheet_url || null,
        manufacturerPartNumber: p.mpn || null,
        manufacturer: p.manufacturer?.name || null,
      };
    }
  } catch (e) {
    errLog(`Octopart поиск не удался: ${e.message}`);
  }
  return null;
}

function extractPartInfo(comp) {
  const info = {
    ref: comp.ref || comp.designator || comp.componentId || comp.name || comp.id || "",
    name: comp.name || comp.value || comp.partName || "",
    type: comp.type || comp.componentType || "",
    manufacturerPartNumber: null,
    jlcpcb: null,
    lcsc: null,
    datasheetUrl: null,
    description: null,
    supplierPartNumbers: null,
  };

  if (!comp || typeof comp !== "object") return info;

  if (comp.datasheetUrl) info.datasheetUrl = comp.datasheetUrl;

  if (comp.supplierPartNumbers && typeof comp.supplierPartNumbers === "object") {
    info.supplierPartNumbers = comp.supplierPartNumbers;
    const spn = comp.supplierPartNumbers;
    if (spn.jlcpcb?.length) info.jlcpcb = spn.jlcpcb[0];
    if (spn.lcsc?.length) info.lcsc = spn.lcsc[0];
    if (spn.mouser?.length) info.manufacturerPartNumber = spn.mouser[0];
    if (spn.digikey?.length) info.manufacturerPartNumber = spn.digikey[0];
  }

  // circuit.json хранит поля в snake_case.
  if (comp.manufacturer_part_number) {
    info.manufacturerPartNumber = comp.manufacturer_part_number;
  }
  // У нас номинал из нетлиста лежит в manufacturerPartNumber, а name —
  // библиотечный идентификатор корпуса. Для поиска датащита нужен именно
  // номинал, поэтому запоминаем оба.
  if (comp.manufacturerPartNumber && !info.manufacturerPartNumber) {
    info.manufacturerPartNumber = comp.manufacturerPartNumber;
  }
  if (comp.mpn && !info.manufacturerPartNumber) info.manufacturerPartNumber = comp.mpn;
  if (comp.description) info.description = comp.description;
  if (!info.name && comp.manufacturerPartNumber) info.name = comp.manufacturerPartNumber;
  if (!info.ref && comp.name) info.ref = comp.name;

  return info;
}

function collectComponents(circuit) {
  const comps = [];

  // circuit.json — ПЛОСКИЙ массив элементов (так его выдают и
  // kicad-to-circuit-json, и tscircuit), а не объект с полями
  // source_components/pcb_components. Поэтому сначала разворачиваем
  // вход в список, а уже потом отбираем нужные типы.
  const items = Array.isArray(circuit) ? circuit : Object.values(circuit ?? {});

  const seen = new Set();
  for (const comp of items) {
    if (!comp || typeof comp !== "object") continue;
    const kind = comp.type;
    if (kind === "source_component") {
      if (comp.Purpose === "power") continue;
      // Название компонента в circuit.json: manufacturer_part_number
      // содержит номинал из нетлиста, name — имя компонента.
      if (seen.has(comp.source_component_id)) continue;
      seen.add(comp.source_component_id);
      comps.push({ comp, kind: "source_component" });
    } else if (kind === "pcb_component") {
      if (comp.pcb_component_type === "via" || comp.pcb_component_type === "copper_pour") continue;
      comps.push({ comp, kind: "pcb_component" });
    }
  }

  return comps;
}

/**
 * Поиск в ЛОКАЛЬНОЙ базе jlcparts (SQLite). Работает без сети и
 * закрывает ~366 тыс. компонентов ссылками на датащиты — это основной
 * источник, сетевые API идут только за тем, чего в базе нет.
 */
function createLocalDbLookup(dbPath) {
  let Database;
  for (const mod of ["better-sqlite3", "node:sqlite"]) {
    try {
      Database = mod === "node:sqlite" ? require(mod).DatabaseSync : require(mod);
      break;
    } catch { /* следующий кандидат */ }
  }
  if (!Database) return null;
  let db;
  try {
    db = new Database(dbPath, { readonly: true });
  } catch (e) {
    errLog(`Локальная база не открылась (${dbPath}): ${e.message}`);
    return null;
  }
  log(`Локальная база: ${dbPath}`);

  const exact = db.prepare(
    `select mfr, lcsc, datasheet, description from jlc_components
      where lower(mfr) = lower(?) and present = 1
        and datasheet is not null and datasheet != ''
      order by preferred desc, stock desc limit 1`,
  );
  const prefix = db.prepare(
    `select mfr, lcsc, datasheet, description from jlc_components
      where lower(mfr) like lower(?) and present = 1
        and datasheet is not null and datasheet != ''
      order by (mfr = lower(?)) desc, preferred desc, stock desc limit 1`,
  );

  return function lookup(mpn) {
    if (!mpn || /[^A-Za-z0-9._+-]/.test(mpn)) return null;
    let row;
    try { row = exact.get(mpn); } catch { return null; }
    if (!row) {
      // У наших ИС MPN не совпадает с кодом производителя
      // (STM32F103C8Tx -> STM32F103VBI6MTR), ищем по устойчивому префиксу.
      const stem = String(mpn).replace(/[^A-Za-z0-9].*$/, "");
      if (stem.length >= 4) {
        try { row = prefix.get(`${stem}%`, stem); } catch { return null; }
      }
    }
    return row ?? null;
  };
}

async function findDatasheet(partInfo, localLookup) {
  if (partInfo.datasheetUrl) {
    log(`  → datasheetUrl уже есть: ${partInfo.datasheetUrl}`);
    return { url: partInfo.datasheetUrl, source: "direct" };
  }

  // 1) ЛОКАЛЬНАЯ БАЗА — без сети. Основной источник.
  if (localLookup) {
    for (const kw of [partInfo.manufacturerPartNumber, partInfo.name]) {
      if (!kw) continue;
      const hit = localLookup(kw);
      if (hit) {
        log(`  → локальная база: ${hit.mfr} (LCSC C${hit.lcsc})`);
        return { url: hit.datasheet, source: "local_jlcparts", partNumber: hit.mfr };
      }
    }
  }

  // 2) Числовой код поставщика из circuit.json
  if (partInfo.jlcpcb) {
    const r = await searchJlcpcb(partInfo.jlcpcb);
    if (r?.datasheetUrl) {
      log(`  → datasheet найден через JLCPCB (по JLC номеру)`);
      return { url: r.datasheetUrl, source: "jlcpcb_by_num", partNumber: r.partNumber };
    }
  }

  if (partInfo.lcsc) {
    const r = await searchLcsc(partInfo.lcsc);
    if (r?.datasheetUrl) {
      log(`  → datasheet найден через LCSC (по LCSC номеру)`);
      return { url: r.datasheetUrl, source: "lcsc_by_num", partNumber: r.lcscNum || r.manufacturer };
    }
  }

  // 3) Сетевой поиск по ключевым словам
  const keywords = [];
  if (partInfo.manufacturerPartNumber) keywords.push(partInfo.manufacturerPartNumber);
  if (partInfo.name && partInfo.name !== partInfo.ref) keywords.push(partInfo.name);
  if (partInfo.ref) keywords.push(partInfo.ref);

  if (partInfo.jlcpcb) {
    const r = await searchJlcpcb(partInfo.jlcpcb);
    if (r?.datasheetUrl) {
      log(`  → datasheet найден через JLCPCB (по JLC номеру)`);
      return { url: r.datasheetUrl, source: "jlcpcb_by_num", partNumber: r.partNumber };
    }
  }

  if (partInfo.lcsc) {
    const r = await searchLcsc(partInfo.lcsc);
    if (r?.datasheetUrl) {
      log(`  → datasheet найден через LCSC (по LCSC номеру)`);
      return { url: r.datasheetUrl, source: "lcsc_by_num", partNumber: r.lcscNum || r.manufacturer };
    }
  }

  for (const kw of keywords) {
    if (OCTOPART_API_KEY) {
      const r = await searchOctopart(kw, partInfo.manufacturerPartNumber || "");
      if (r?.datasheetUrl) {
        log(`  → datasheet найден через Octopart`);
        return { url: r.datasheetUrl, source: "octopart", partNumber: r.manufacturerPartNumber };
      }
    }

    const r = await searchLcsc(kw);
    if (r?.datasheetUrl) {
      log(`  → datasheet найден через LCSC (по "${kw}")`);
      return { url: r.datasheetUrl, source: "lcsc_by_keyword", partNumber: r.lcscNum || r.manufacturer };
    }

    const r2 = await searchJlcpcb(kw);
    if (r2?.datasheetUrl) {
      log(`  → datasheet найден через JLCPCB (по "${kw}")`);
      return { url: r2.datasheetUrl, source: "jlcpcb_by_keyword", partNumber: r2.partNumber };
    }
  }

  return null;
}

async function downloadFile(url, destPath, timeout = DOWNLOAD_TIMEOUT) {
  let lastError = null;
  for (let attempt = 1; attempt <= MAX_RETRIES; attempt++) {
    try {
      const proto = url.startsWith("https") ? https : http;
      const req = proto.get(url, { timeout }, (res) => {
        if (res.statusCode !== 200) {
          throw new Error(`HTTP ${res.statusCode}`);
        }
        const file = fs.createWriteStream(destPath);
        res.pipe(file);
        file.on("finish", () => {
          file.close();
          const size = fs.statSync(destPath).size;
          log(`✓ Скачано: ${path.basename(destPath)} (${size} байт)`);
        });
      });
      await new Promise((resolve, reject) => {
        req.on("error", reject);
        req.on("timeout", () => { req.destroy(); reject(new Error("Таймаут скачивания")); });
      });
      return;
    } catch (e) {
      lastError = e;
      if (attempt < MAX_RETRIES) {
        log(`⚠ Повтор ${attempt}/${MAX_RETRIES}: ${e.message}`);
        await new Promise((r) => setTimeout(r, 1000 * attempt));
      }
    }
  }
  throw lastError;
}

async function run(circuitPath, outputDir) {
  ensureDir(outputDir);

  log(`Чтение circuit.json: ${circuitPath}`);
  if (!fs.existsSync(circuitPath)) {
    errLog(`Файл не найден: ${circuitPath}`);
    return { success: 0, failed: 0, skipped: 0 };
  }

  let circuit;
  try {
    circuit = JSON.parse(fs.readFileSync(circuitPath, "utf8"));
  } catch (e) {
    errLog(`Ошибка парсинга JSON: ${e.message}`);
    return { success: 0, failed: 0, skipped: 0 };
  }

  const components = collectComponents(circuit);
  log(`Найдено компонентов: ${components.length}`);

  let success = 0;
  let failed = 0;
  let skipped = 0;

  const queue = [...components];
  let idx = 0;

  const workers = Array(CONCURRENCY).fill(null).map(async () => {
    while (true) {
      let item;
      synchronized(() => {
        if (idx >= queue.length) { item = null; return; }
        item = queue[idx++];
      });
      if (!item) break;

      const partInfo = extractPartInfo(item.comp);
      const ref = partInfo.ref || "unknown";

      log(`[${item.kind}] ${ref} "${partInfo.name}"`);

      if (partInfo.datasheetUrl) {
        const dest = path.join(outputDir, `${sanitize(ref)}_ds.pdf`);
        try {
          await downloadFile(partInfo.datasheetUrl, dest);
          success++;
        } catch (e) {
          errLog(`✗ Ошибка скачивания: ${e.message}`);
          failed++;
        }
        continue;
      }

      const found = await findDatasheet(partInfo);
      if (found?.url) {
        const fn = `${sanitize(ref)}_${sanitize(found.partNumber || found.source || "ds")}.pdf`;
        const dest = path.join(outputDir, fn);
        try {
          await downloadFile(found.url, dest);
          log(`  Source: ${found.source}`);
          if (found.partNumber) log(`  MPN: ${found.partNumber}`);
          success++;
        } catch (e) {
          errLog(`✗ Ошибка скачивания: ${e.message}`);
          failed++;
        }
      } else {
        log(`  ⊘ Датащит не найден`);
        skipped++;
      }
    }
  });

  await Promise.all(workers);

  log(`\nГотово! Скачано: ${success}, Ошибок: ${failed}, Пропущено: ${skipped}`);
  log(`Результаты в: ${path.resolve(outputDir)}`);

  const summary = path.join(outputDir, "download-summary.json");
  fs.writeFileSync(summary, JSON.stringify({
    timestamp: new Date().toISOString(),
    circuit: circuitPath,
    outputDir,
    success,
    failed,
    skipped,
    octopartEnabled: !!OCTOPART_API_KEY,
  }, null, 2));

  return { success, failed, skipped };
}

function synchronized(fn) {
  return fn();
}

async function main() {
  const circuitPath = path.resolve(process.argv[2] || DEFAULT_CIRCUIT);
  const outputDir = path.resolve(process.argv[3] || DEFAULT_OUT_DIR);

  log(`Запуск скачивания датащитов`);
  log(`Circuit: ${circuitPath}`);
  log(`Output:  ${outputDir}`);
  log(`Concurrency: ${CONCURRENCY}`);
  log(`Octopart API: ${OCTOPART_API_KEY ? "включено" : "отключено"}`);
  log("");

  const result = await run(circuitPath, outputDir);
  process.exit(result.failed > 0 ? 1 : 0);
}

// Если запущен напрямую
if (process.argv[1] === fileURLToPath(import.meta.url) ||
    process.argv[1]?.endsWith("download-datasheets.js")) {
  main().catch((e) => {
    errLog(`Фатальная ошибка: ${e.message}`);
    process.exit(1);
  });
}

export {
  run,
  downloadFile,
  httpGet,
  searchJlcpcb,
  searchLcsc,
  searchOctopart,
  extractPartInfo,
  collectComponents,
  findDatasheet,
  sanitize,
  ensureDir,
};
