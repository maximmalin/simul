# @tscircuit/create-fdm-enclosure

A staged geometry solver that turns board dimensions and part-owned apertures
into a two-part FDM enclosure. It returns serializable JSCAD plans for a base,
a lid, and an assembled preview.

Implemented today:

- inferred or explicit enclosure dimensions;
- separate base and lid with a friction-fit lip;
- component-relative apertures on all six faces;
- oblique side-wall cuts and first-wall intersection at corners;
- aperture depth derived from measured component bodies;
- injectable FDM design rules; and
- one typed `cad_fdm_enclosure` record per printed part through core.

Mounting bosses, inserts, fasteners, assembly DRC, full component-body clearance,
and non-FDM processes remain future work.

## Circuit authoring

Renderable `assembly` and `enclosure` elements come from `tscircuit` (or
`@tscircuit/core`), not from this geometry package:

```tsx
import { assembly, enclosure } from "tscircuit"

export default () => (
  <assembly.device name="controller">
    <board name="B1" width="50mm" height="36mm">
      <connector
        name="J1"
        pcbX="22mm"
        footprint={
          <footprint insertionDirection="from_right">
            {/* pads */}
          </footprint>
        }
      >
        <enclosure.cutoutaperture
          shape="pill"
          width="9.2mm"
          height="3.3mm"
          margin="0.2mm"
        />
      </connector>
    </board>

    <enclosure.fdm.box boardRef=".B1" />
  </assembly.device>
)
```

A part that is installed in one direction but interacted with in another should
declare both footprint directions:

```tsx
<switch
  footprint={
    <footprint
      insertionDirection="from_above"
      cutoutApertureDirection="from_right"
    >
      {/* pads */}
    </footprint>
  }
>
  <enclosure.cutoutaperture shape="rect" width="3mm" height="2mm" />
</switch>
```

Here the switch is mounted from above, but its actuator exits through a side
wall. Overloading `insertionDirection` would make one of those statements false.

## How aperture placement works

### Direction precedence

Core chooses the part-owned interaction axis in this order:

1. `cutoutApertureDirection` — where the enclosure opening faces;
2. `insertionDirection` — where a cable or mating part attaches; then
3. nearest board edge — a square-to-wall fallback when no direction is authored.

Both direction props use the same footprint-local vocabulary and the same
rotation/layer transform as the footprint geometry:

| Direction | Footprint-local axis |
| --- | --- |
| `from_right` / `from_left` | +X / -X |
| `from_top` / `from_bottom` | +Y / -Y |
| `from_above` / `from_below` | +Z / -Z |

The deprecated `from_front`/`from_back` names are accepted as input but are
normalized and never emitted.

### Component axis, then enclosure face

The direction is transformed into a continuous board-space axis through
`pcb_component.center`, the same datum the component body rotates around.

- For a side opening, the solver cuts the first cavity wall that ray intersects.
  The face transition therefore occurs where the physical axis crosses a box
  corner; it is not fixed at 45 degrees.
- `from_above` and `from_below` select the lid or floor. A bottom-layer flip is a
  180-degree rotation about board Y, so it also flips the Z direction.
- The quantized Cartesian direction is retained as an exact-corner tie-breaker.

Enclosure faces name their outward normal explicitly:

| Face | Normal |
| --- | --- |
| `x_pos`, `x_neg` | +X, -X |
| `y_pos`, `y_neg` | +Y, -Y |
| `z_pos`, `z_neg` | lid +Z, floor -Z |

`boardSide` is different vocabulary: it names the PCB layer and remains
`"top" | "bottom"`.

### Aperture dimensions

Aperture geometry is authored around the component interaction axis:

- side opening: `height` is board Z, `width` is perpendicular to the axis in the
  board plane, and `depth` follows the axis inboard;
- lid/floor opening: `width` and `height` rotate with the footprint and `depth`
  is vertical; and
- circle: `radius` defines the profile.

An oblique cylinder naturally makes an elliptical wall intersection. The solver
extends the cutter enough to cross both wall surfaces without changing the
authored component-relative depth. Fit validation uses the projected oblique
width, permits a profile to wrap a corner, and rejects profiles that either miss
or engulf an enclosure span.

`widthDimensionOffset` and `heightDimensionOffset` are placement corrections.
On a side wall they move the resolved intersection along the wall and away from
the mounting surface; on the lid or floor they rotate in-plane with the profile.

### Depth

`depth` is additional inboard reach beyond the pierced plate's inner surface. It
is never silently capped, so an explicitly excessive depth may reach the far
shell.

When omitted, core may provide a `componentBody` envelope. Side depth is derived
from its rotated body/footprint extent. Horizontal depth is the cavity span from
the plate's inner surface to the component mounting plane; the cutter already
contains the lid or floor thickness, so that thickness is not counted twice.
Measured `modelBounds` plus `modelOriginPosition` provide the most accurate
`aboveBoardHeight`; `size.z` is only a fallback.

## Enclosure sizing and defaults

All solver API numbers are millimetres. Authoring props use tscircuit `Distance`
values, so strings such as `"3.5mm"` and `"0.25in"` are parsed by core before
reaching this package.

| Input | Default / inference |
| --- | --- |
| `width`, `height` | board span + board clearance + walls |
| `depth` | floor + standoff + board + headroom + lid |
| `wallThickness` | 2 mm |
| `floorThickness`, `lidThickness` | wall thickness |
| `boardClearance` | 1 mm per side |
| `standoffHeight` | 4 mm |
| `topHeadroom` | 6 mm |
| `lidLipDepth` | 4 mm, clamped to the available cavity |

Omitted `topHeadroom` allows side-aperture component envelopes to grow the box.
An explicit value is literal. It is clearance above the PCB, not above the
tallest arbitrary component; parts without apertures do not report an envelope
to this solver.

## Solver API

```ts
import {
  createFdmEnclosure,
  type CreateFdmEnclosureInput,
} from "@tscircuit/create-fdm-enclosure"

const input: CreateFdmEnclosureInput = {
  board: { width: 40, height: 24, thickness: 1.6 },
  apertures: [
    {
      shape: "pill",
      face: "y_pos",
      width: 9,
      height: 3.6,
      margin: 0.5,
      center: { x: 0, y: 8 },
    },
  ],
}

const output = createFdmEnclosure(input)

output.parts // [{ id: "base", jscadPlan }, { id: "lid", jscadPlan }]
output.jscadPlan // assembled preview plan
output.dimensions
output.frame
```

Use `CreateFdmEnclosureSolver` directly when a debugger or lifecycle integration
needs `step()`, `visualize()`, constructor parameters, or stage metadata.

### Coordinate frames

The solver's local enclosure frame is centered on X/Y, with the outside floor at
Z = 0 and the complete box in positive Z. Aperture X/Y inputs are board
coordinates relative to the board center.

Circuit world instead puts the board center plane at Z = 0. Core translates each
finished enclosure part by:

```text
-boardThickness / 2 - floorThickness - standoffHeight
```

so the two frames coincide in the assembled product.

Every aperture tool is subtracted from both base and lid. Geometry decides which
part it intersects: a lid-only tool misses the base, a floor-only tool misses the
lid, and a side opening crossing the seam naturally splits across both.

## Current coordinated branches

The pure solver can be developed independently, but the complete authored and
rendered feature currently spans unreleased branches:

| Repository branch | Capability |
| --- | --- |
| [`circuit-json:feat/parametric-enclosures`](https://github.com/addibble/circuit-json/tree/feat/parametric-enclosures) | assembly/enclosure source records, per-part `cad_fdm_enclosure`, `model_bounds`, and `cutout_aperture_direction` |
| [`props:feat/parametric-enclosures`](https://github.com/addibble/props/tree/feat/parametric-enclosures) | authoring schemas including `cutoutApertureDirection`, `enclosure.*`, and `assembly.device` |
| [`circuit-json-to-gltf:feat/parametric-enclosures`](https://github.com/addibble/circuit-json-to-gltf/tree/feat/parametric-enclosures) | `cad_fdm_enclosure` conversion to GLB/glTF |
| [`3d-viewer:feat/parametric-enclosures`](https://github.com/addibble/3d-viewer/tree/feat/parametric-enclosures) | direct enclosure rendering and viewer-owned base/lid appearance state |
| [`core:feat/parametric-enclosures`](https://github.com/addibble/core/tree/feat/parametric-enclosures) | host elements, direction transforms, solver orchestration, and per-part record emission |

`infer-cable-insertion-point` is **not** a required fork: its canonical direction
support is already published. Packing/reposition transforms for
`cutout_aperture_direction` are tracked separately in
[`circuit-json-util#114`](https://github.com/tscircuit/circuit-json-util/pull/114).

Release in dependency order:

1. `circuit-json`;
2. `props` and `circuit-json-util`;
3. `circuit-json-to-gltf` and `3d-viewer`;
4. `create-fdm-enclosure`;
5. `core`; then
6. rebuild inlining consumers (`eval`, `runframe`, CLI, and `tscircuit`).

Until those releases exist, use the sibling checkouts with yalc:

```bash
./tsc-dev rebuild --from create-fdm-enclosure
```

## FAQ

### Why have both `cutoutApertureDirection` and `insertionDirection`?

Installation/mating direction and interaction direction are different physical
facts. They coincide for most connectors and differ for parts such as
side-actuated switches. The aperture-specific direction wins when both exist.

### Can I set the enclosure face directly on `<enclosure.cutoutaperture>`?

No. A reusable part knows its own local direction, not which enclosure wall a
particular board placement will reach. Position and rotation determine the face.
The low-level solver accepts an initial `face` because adapters without a
continuous axis still need a square-to-wall fallback.

### When is aperture direction auto-detected?

Normally it is not. A footprint's `cutoutApertureDirection` or
`insertionDirection` defines the aperture axis in the part-local frame, and that
axis moves and rotates with the part.

Only when both properties are absent does core guess from the nearest board
edge. Moving the part can then change the detected direction and wall. With an
authored direction, the direction itself is never re-guessed; near a box corner
the resolved wall can still change if the same physical axis reaches a different
wall first.

### Is enclosure translucency a prop or Circuit JSON field?

No. It changes only display, not geometry or manufacturing output. The 3D viewer
owns separate hidden/translucent/opaque state for base and lid.

### Does `topHeadroom` clear every component?

No. It is measured from the PCB top surface. Only parts that own apertures report
body envelopes to the enclosure solver; general component clearance requires
future assembly/enclosure DRC.

### Why can an authored depth cut the opposite shell?

Authored depth is intentional geometry and is never silently shortened. Omit it
to use the safe derived datum, or reduce it if the far-shell cut is unintended.

### Why does an open browser tab sometimes show code from before a rebuild?

RunFrame's eval worker is embedded in the standalone bundle and remains alive for
the lifetime of the page. After rebuilding, close old tabs and load a fresh
`http://localhost:3020/` page.

## Development

```bash
bun install
bunx tsc --noEmit
bun test
bun run format
bun run build
bun run start       # Cosmos solver debugger
```

Internal dependency direction:

```text
lib/assembly/   assembly frame; imports no enclosure/process layer
      ↓
lib/enclosure/  process-independent faces, apertures, component envelopes
      ↓
lib/apertures/  profile and cutting-tool construction
      ↓
lib/fdm/        FDM dimensions, lip, shell, rules, composition
```

`resolveFdmEnclosureProblem()` is the single resolution pass. Construction stages
consume its resolved output and must not independently reapply defaults or
placement fallbacks.
