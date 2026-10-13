/**
 * Сборка просмотрщика 3D-модели (hardware/tscircuit/viewer/main.js) в один
 * IIFE-файл без внешних зависимостей.
 *
 *   node_modules/.bin/rollup -c hardware/tscircuit/viewer/rollup.config.mjs
 *
 * Зачем отдельный конфиг, а не CLI: три.js поставляет и неминифицированную
 * сборку (exports."." -> build/three.module.js), и минифицированную. Для
 * встраивания в одну HTML-страницу нужен компактный вариант, поэтому 'three'
 * явно перенаправляется на build/three.module.min.js; аддоны (GLTFLoader,
 * OrbitControls) тянутся из examples/jsm и сами импортируют 'three'.
 */
import { defineConfig } from "rollup"
import { nodeResolve } from "@rollup/plugin-node-resolve"
import { fileURLToPath } from "node:url"
import { resolve, dirname } from "node:path"

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, "../../..")

/** 'three' -> минифицированная сборка; остальное резолвит node-resolve. */
const threeMinified = {
  name: "three-minified",
  resolveId(source) {
    if (source === "three") {
      return resolve(ROOT, "node_modules/three/build/three.module.min.js")
    }
    return null
  },
}

export default defineConfig({
  input: resolve(HERE, "main.js"),
  plugins: [threeMinified, nodeResolve({ browser: true, preferBuiltins: false })],
  output: {
    file: resolve(HERE, "dist/krystal-viewer.js"),
    format: "iife",
    name: "KrystalViewer",
    compact: true,
  },
})
