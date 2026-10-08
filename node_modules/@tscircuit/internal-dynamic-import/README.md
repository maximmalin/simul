# @tscircuit/internal-dynamic-import

This module simplifies dynamically importing tscircuit modules, especially when you always want to use the latest version.

It is also approximately type-safe, meaning we package bundled declaration types for a recent version of the supported modules here while leaving the runtime implementation remote.

```tsx
import importer from "@tscircuit/internal-dynamic-import"

async function main() {
  const { convertSoupToGerberCommands } = await importer("circuit-json-to-gerber")

  // Import a specific version
  const { CircuitJsonToKicadProConverter } = await importer(
    "circuit-json-to-kicad@0.0.91",
  )

  // ...
}
```

Runtime imports try jscdn first through `https://jscdn.tscircuit.com/.../+esm`
and fall back to `https://esm.run/...` if the jscdn import fails. Omitting a
version uses `latest`; adding `@...` preserves the requested version/tag.
