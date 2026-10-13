#!/usr/bin/env node
/**
 * Детерминированная headless-проверка страницы out/krystal-radio.html.
 *
 * Chrome с --virtual-time-budget завершает dump-dom по виртуальному времени
 * и не дожидается асинхронного разбора GLB (декодирование текстур идёт на
 * отдельном потоке), поэтому результат нестабилен. Здесь то же самое
 * делается через DevTools Protocol: браузер запускается с
 * --remote-debugging-port, страница открывается, после чего мы ждём
 * реального события `model-loaded` опросом DOM.

 * Печатает одну строку JSON:
 *   {"canvas":bool,"state":"loaded|error|pending|no-viewer","error":str?}
 *
 * Запуск:  node tools/page_render_check.mjs <file-or-http-url>
 * Код возврата: 0 — модель загружена, 1 — нет/ошибка, 2 — нет chrome/node.
 */
import { spawn, spawnSync } from "node:child_process"
import { mkdtempSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

const url = process.argv[2]
if (!url) {
  console.error("usage: page_render_check.mjs <url>")
  process.exit(2)
}
const TIMEOUT_MS = Number(process.env.RENDER_TIMEOUT_MS ?? 90000)

function which(names) {
  for (const n of names) {
    try {
      const r = spawnSync("which", [n], { encoding: "utf8" })
      if (r.status === 0 && r.stdout.trim()) return r.stdout.trim()
    } catch { /* ignore */ }
  }
  return null
}

const chrome = process.env.CHROME_PATH || which([
  "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
])
if (!chrome) {
  console.error("page_render_check: Chrome/Chromium не найден")
  process.exit(2)
}

const profile = mkdtempSync(join(tmpdir(), "krystal-render-"))
const proc = spawn(chrome, [
  "--headless=new",
  "--no-sandbox",
  "--disable-gpu",
  "--disable-dev-shm-usage",
  "--remote-debugging-port=0",
  `--user-data-dir=${profile}`,
  url,
], { stdio: ["ignore", "ignore", "pipe"] })

function cleanup(code) {
  try { proc.kill("SIGKILL") } catch { /* ignore */ }
  try { rmSync(profile, { recursive: true, force: true }) } catch { /* ignore */ }
  process.exit(code)
}

// Chrome печатает "DevTools listening on ws://127.0.0.1:PORT/devtools/browser/..."
let buf = ""
proc.stderr.on("data", (d) => {
  buf += d.toString()
  const m = buf.match(/DevTools listening on (ws:\/\/[^\s]+)/)
  if (m) start(m[1])
})
proc.on("exit", (c) => {
  if (!started) { console.error(`chrome вышел с кодом ${c}`); cleanup(2) }
})

let started = false
async function start(browserWs) {
  if (started) return
  started = true
  const port = new URL(browserWs).port
  const deadline = Date.now() + TIMEOUT_MS

  // Находим WebSocket URL самой страницы (target type=page).
  let targetWs = null
  while (Date.now() < deadline && !targetWs) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
      const page = list.find((t) => t.type === "page" && t.webSocketDebuggerUrl)
      if (page) targetWs = page.webSocketDebuggerUrl
    } catch { /* порт ещё не готов */ }
    if (!targetWs) await sleep(150)
  }
  if (!targetWs) { console.error("не найден target страницы"); cleanup(2) }

  const ws = new WebSocket(targetWs)
  let nextId = 1
  const pending = new Map()
  ws.addEventListener("message", (ev) => {
    let msg
    try { msg = JSON.parse(ev.data) } catch { return }
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg)
      pending.delete(msg.id)
    }
  })
  const send = (method, params = {}) =>
    new Promise((res) => {
      const id = nextId++
      pending.set(id, res)
      ws.send(JSON.stringify({ id, method, params }))
    })

  await new Promise((res, rej) => {
    ws.addEventListener("open", res, { once: true })
    ws.addEventListener("error", rej, { once: true })
  })
  await send("Runtime.enable")

  const probe = `(() => {
    const v = document.querySelector('#viewer');
    const canvas = document.querySelector('#viewer canvas');
    if (!v) return JSON.stringify({ state: 'no-viewer', canvas: !!canvas });
    if (v.getAttribute('data-model-loaded') === '1') return JSON.stringify({ state: 'loaded', canvas: !!canvas });
    const f = document.querySelector('.viewer-fallback');
    if (f) return JSON.stringify({ state: 'error', canvas: !!canvas, error: f.textContent.slice(0, 200) });
    return JSON.stringify({ state: 'pending', canvas: !!canvas });
  })()`

  let last = { state: "pending", canvas: false }
  while (Date.now() < deadline) {
    const r = await send("Runtime.evaluate", { expression: probe, returnByValue: true })
    try {
      last = JSON.parse(r.result.result.value)
    } catch { /* ignore transient */ }
    if (last.state === "loaded" || last.state === "error") break
    await sleep(250)
  }

  console.log(JSON.stringify(last))
  const ok = last.state === "loaded" && last.canvas === true
  cleanup(ok ? 0 : 1)
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)) }
