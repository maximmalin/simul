# @tscircuit/manifold-2d

Zero-dependency access to Manifold's WebAssembly geometry core for robust 2D
polygon operations.

The upstream `manifold-3d` npm package also ships CAD, glTF, and 3MF tooling.
Those tools bring native image-processing dependencies into every install even
when only `CrossSection` is used. This package vendors only the upstream core
JavaScript, WebAssembly, and TypeScript declarations.

## Installation

Install the package from npm:

```sh
bun add @tscircuit/manifold-2d
```

## Usage

```ts
import { getManifoldModule } from "@tscircuit/manifold-2d"

const { CrossSection } = await getManifoldModule()
const outline = CrossSection.ofPolygons([
  [
    [0, 0],
    [10, 0],
    [10, 10],
    [0, 10],
  ],
])
const inset = outline.offset(-0.2, "Miter")
```

The first call to `getManifoldModule()` initializes a shared WASM instance.
After initialization, `getManifoldModuleSync()` returns that instance.
Node loads the vendored `.wasm` file directly. Browser and worker bundles use
the package's embedded WASM fallback, so the default API does not require an
asset-loader configuration.

To serve the WASM as a separate asset instead, point the loader at the emitted
asset before initialization:

```ts
import { setWasmUrl } from "@tscircuit/manifold-2d"
import wasmUrl from "@tscircuit/manifold-2d/manifold.wasm?url"

setWasmUrl(wasmUrl)
```

## Updating Manifold

The vendored files currently come from `manifold-3d@3.5.1`. Their source
version, npm integrity, and SHA-256 hashes are recorded in
`vendor/manifest.json`.

```sh
npm run vendor:manifold -- 3.5.1
npm run verify:vendor
npm test
```

The vendored Manifold files retain their upstream copyright headers and are
distributed under the Apache License 2.0 included in `LICENSE`.
