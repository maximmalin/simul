import createManifoldModule from "./vendor/manifold.js"
import embeddedWasmBase64 from "./vendor/manifold.wasm.base64.js"

let manifoldModule = null
let manifoldModulePromise = null
let wasmUrl = null

export { createManifoldModule }

export const setWasmUrl = (url) => {
  wasmUrl = String(url)
}

const decodeEmbeddedWasm = () => {
  const binary = globalThis.atob(embeddedWasmBase64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index++) {
    bytes[index] = binary.charCodeAt(index)
  }
  return bytes
}

export const instantiateManifold = async () => {
  const isNode = Boolean(
    globalThis.process?.versions?.node &&
      globalThis.process?.type !== "renderer",
  )
  const options = wasmUrl
    ? { locateFile: () => wasmUrl }
    : isNode
      ? undefined
      : {
          // Emscripten resolves a filename before consulting wasmBinary. A
          // locateFile hook prevents its bundled `new URL(..., import.meta.url)`
          // fallback from running when import.meta.url is unavailable.
          locateFile: () => "manifold.wasm",
          wasmBinary: decodeEmbeddedWasm(),
        }
  const module = await createManifoldModule(options)
  module.setup()
  return module
}

export const getManifoldModule = async () => {
  if (manifoldModule) return manifoldModule

  manifoldModulePromise ??= instantiateManifold()
    .then((module) => {
      manifoldModule = module
      return module
    })
    .catch((error) => {
      manifoldModulePromise = null
      throw error
    })

  return manifoldModulePromise
}

export const getManifoldModuleSync = () => manifoldModule
