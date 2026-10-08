# modelprinter

Parametric CAD model strings and renderer-independent Zod schemas for
tscircuit.

`modelprinter` is an experimental companion to
[`footprinter`](https://github.com/tscircuit/footprinter). The first model is
`flexscreen`; the API deliberately resembles `fp.string(...)` so tscircuit can
initially pass these strings through its existing `cadModel` footprinter-string
path.

```ts
import { modelprinter } from "@tscircuit/modelprinter"

const model = modelprinter
  .string(
    "flexscreen_w40mm_h22.5mm_flex60mm_foldsabove_distance20mm_outset6mm",
  )
  .json()

// {
//   fn: "flexscreen",
//   width: 40,
//   height: 22.5,
//   flexCableLength: 60,
//   orientation: "foldedToFaceAboveBoard",
//   distanceAboveBoard: 20,
//   foldOutset: 6,
// }
```

As with footprinter, `.params()` exposes the unvalidated builder parameters so
callers can inspect `fn` before choosing a renderer:

```ts
modelprinter.string("flexscreen_w40mm_foldsabove").params()
// { flexscreen: true, fn: "flexscreen", w: "40mm", foldsabove: true, ... }
```

## Zod schemas

`flexScreenModelPropsSchema` models the complete public FlexScreen surface:
screen sizing, orientation shortcuts, flex and conductor geometry, fold
geometry, board references, colors, visibility, offsets, and rotations.
Lengths accept millimeter numbers or unit-bearing strings and normalize to
millimeters.

```ts
import { flexScreenModelPropsSchema } from "@tscircuit/modelprinter"

const props = flexScreenModelPropsSchema.parse({
  diagonal: "2in",
  ratio: "16:9",
  foldsBelowBoard: true,
  distanceBelowBoard: "8mm",
  foldOutset: "5mm",
})
```

The package also exports `FlexScreenModelPropsInput`,
`FlexScreenModelProps`, `FlexScreenModelDefinition`, and their constituent
schemas.

## Initial string grammar

Strings begin with `flexscreen`. Underscore-separated tokens set properties:

- Size: `w40mm`, `h22.5mm`, `diag2in`, `ratio16x9`
- Cable: `flex60mm`, `flexwidth10mm`, `conductors10`
- Orientation: `sitsflat`, `foldsabove`, `foldsbelow`, `rightangleabove`,
  `rightanglebelow`
- Fold: `distance20mm`, `foldstart9mm`, `outset6mm`, `foldsegments24`
- Visibility: `hidescreen`, `hideflex`, `hideconductors`, `hidestiffeners`
- Colors: `screencolor(#112233)`, `flexcolor(orange)`

The shorthand `distance` is only valid with `foldsabove` or `foldsbelow`, and
maps to the corresponding above/below property. Unknown tokens throw rather
than being ignored.

## Development

```sh
bun install
bun test
bun run typecheck
bun run build
```
