/**
 * Скриншот работающего интерфейса через Chrome DevTools Protocol.
 *
 *   node tools/screenshot_ui.mjs <url> <out.png> [tab]
 *
 * Пример:
 *   node tools/screenshot_ui.mjs http://localhost:5173/ out/krystal-tab.png krystal
 *
 * Нужен установленный Chrome/Chromium. Использует глобальный WebSocket
 * и fetch из Node 22 — внешних зависимостей нет.
 */

import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const URL_ = process.argv[2] ?? 'http://localhost:5173/';
const OUT = process.argv[3] ?? 'out/ui.png';
const TAB = process.argv[4] ?? 'krystal';
const WIDTH = Number(process.argv[5] ?? 1600);
const HEIGHT = Number(process.argv[6] ?? 1100);

const CHROME = process.env.CHROME_BIN ?? '/usr/bin/google-chrome';
const PORT = 9222 + Math.floor(process.pid % 500);

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function main() {
  const profile = mkdtempSync(join(tmpdir(), 'cdp-shot-'));
  const chrome = spawn(CHROME, [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${profile}`,
    '--no-sandbox',
    '--disable-gpu',
    '--disable-dev-shm-usage',
    '--hide-scrollbars',
    `--window-size=${WIDTH},${HEIGHT}`,
    'about:blank',
  ], { stdio: ['ignore', 'ignore', 'pipe'] });

  // дождаться devtools endpoint
  let target = null;
  for (let i = 0; i < 60 && !target; i++) {
    await sleep(250);
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      target = list.find((t) => t.type === 'page');
    } catch { /* ещё не поднялся */ }
  }
  if (!target) {
    chrome.kill();
    throw new Error('Chrome DevTools не поднялся');
  }

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();

  ws.addEventListener('message', (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id)(msg);
      pending.delete(msg.id);
    }
  });

  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true });
    ws.addEventListener('error', rej, { once: true });
  });

  const send = (method, params = {}) =>
    new Promise((res) => {
      const myId = ++id;
      pending.set(myId, res);
      ws.send(JSON.stringify({ id: myId, method, params }));
    });

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: WIDTH, height: HEIGHT, deviceScaleFactor: 2, mobile: false,
  });

  await send('Page.navigate', { url: URL_ });
  await sleep(3500);

  // переключиться на нужную вкладку, если указана
  if (TAB) {
    for (let attempt = 0; attempt < 3; attempt++) {
      const { result: r } = await send('Runtime.evaluate', {
        expression: `(() => {
          const btn = [...document.querySelectorAll('nav button')].find(el =>
            (el.textContent || '').toLowerCase().includes(${JSON.stringify(TAB.toLowerCase())}));
          if (!btn) return 'not-found';
          btn.click();
          return 'clicked';
        })()`,
        returnByValue: true,
      });
      const v = r?.result?.value;
      if (v === 'clicked') break;
      console.warn(`tab "${TAB}" -> ${v} (attempt ${attempt + 1})`);
      await sleep(1500);
    }
    await sleep(2500);
  }

  // скрыть «оффлайн»-заглушки Supabase, если они есть
  const { result } = await send('Runtime.evaluate', {
    expression: 'document.body.innerText.slice(0, 300)',
    returnByValue: true,
  });

  const shot = await send('Page.captureScreenshot', { format: 'png' });
  const buf = Buffer.from(shot.result.data, 'base64');
  writeFileSync(OUT, buf);

  console.log(`saved: ${OUT} (${buf.length} bytes)`);
  console.log(`page text: ${(result?.value ?? '').replace(/\s+/g, ' ').slice(0, 160)}`);

  ws.close();
  chrome.kill();
}

main().catch((e) => {
  console.error('screenshot failed:', e.message);
  process.exit(1);
});