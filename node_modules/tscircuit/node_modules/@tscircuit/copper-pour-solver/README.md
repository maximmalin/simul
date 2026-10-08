# @tscircuit/copper-pour-solver

Solves PCB copper pour regions from Circuit JSON or from a small geometry input
format, returning `pcb_copper_pour`-ready B-Rep shapes.

## Install

```bash
bun add @tscircuit/copper-pour-solver
```

This package expects TypeScript 5 as a peer dependency.

## Basic Usage With Circuit JSON

Initialize the geometry runtime once before solving. Then convert Circuit JSON
into the solver input format, run the solver, and map the returned B-Rep shapes
back into `pcb_copper_pour` elements.

```ts
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
  initializeManifoldGeometry,
} from "@tscircuit/copper-pour-solver"

await initializeManifoldGeometry()

const inputProblem = convertCircuitJsonToInputProblem(circuitJson, {
  layer: "top",
  source_net_name: "GND",
  pad_margin: 0.4,
  trace_margin: 0.2,
  board_edge_margin: 0.1,
  cutout_margin: 0.2,
  use_thermal_reliefs: true,
  thermal_relief_spoke_width: 0.3,
  thermal_relief_spoke_count: 4,
})

const solver = new CopperPourPipelineSolver(inputProblem)
const { brep_shapes } = solver.getOutput()
```

`convertCircuitJsonToInputProblem` reads board bounds or outline, SMT pads,
plated holes, mechanical holes, vias, traces, and cutouts for the selected layer.
Pads and traces connected to the selected source net are kept connected to the
pour; unrelated geometry is subtracted using the configured margins.
When `use_thermal_reliefs` is true, plated holes and SMT pads on the pour net are
isolated by the `pad_margin` air gap and reconnected by evenly spaced spokes.
`thermal_relief_spoke_width` is required and `thermal_relief_spoke_count`
defaults to 4. Same-net vias and traces remain solid-connected.

Pass an options array to solve all pours from one subcircuit together. This is
required for pour-to-pour clearance because the solver must see every region in
the same input problem. Array order is the priority order: later pours take
priority over earlier pours.

## Crosshatched Copper

Set `crosshatch: true` on a converter options object or an `InputPourRegion` to
generate a 45-degree mesh with 0.25 mm copper width and 1 mm center-to-center
pitch by default. Set `crosshatchPitch` and `crosshatchWidth` in millimetres to
customize the mesh, for example `{ crosshatch: true, crosshatchPitch: 2,
crosshatchWidth: 0.4 }`. Both must be finite and positive, with width less than
pitch. Omission or `false` keeps solid fill regardless of sizing options. The
mesh is anchored at board-world (0, 0), in millimetres (+X right, +Y up), so
neighboring regions with the same pitch share a grid.

Square openings have side length `crosshatchPitch - crosshatchWidth` and are
clipped at pour edges, retaining triangles, trapezoids and other partial cells
behind a `crosshatchWidth` solid copper rim. Board-edge clearance is applied
before this rim. Each connected opening fragment is removed only if its area is
at least `crosshatchWidth²` and an inward offset of `crosshatchWidth / 2` leaves
a nonempty interior; smaller fragments and long thin slivers remain solid
copper. These filters scale with the mesh width, not a manufacturer-specific DRC.
The defaults retain the previous 0.75 mm openings, 0.25 mm rim, 0.0625 mm²
minimum area and 0.125 mm inset. A request exceeding 1,000,000 candidate grid
cells fails with an error asking for a larger pitch or smaller pour area.

Cells intersecting protected same-net pads/vias/traces or obstacle clearances
(including thermal reliefs, holes, keepouts and higher-priority pours) are still
omitted entirely, with `crosshatchWidth` additional separation. This preserves connections
and avoids isolating copper around an obstacle inside a cell. Narrow regions
can remain solid; existing disconnected islands retain ordinary solid-pour
behavior.

Clearances and pour priority are resolved before hatching. Higher-priority
crosshatched regions reserve their openings against lower-priority different-net
pours. Output remains ordinary B-Rep geometry with inner rings; renderers and
fabrication exporters should consume those rings rather than regenerate a grid.

## Selecting The Pour Net

Prefer selecting by source net name or id:

```ts
const inputProblem = convertCircuitJsonToInputProblem(circuitJson, {
  layer: "top",
  source_net_name: "GND",
  pad_margin: 0.4,
  trace_margin: 0.2,
})
```

You can also pass the source net's stable `subcircuit_connectivity_map_key`
directly:

```ts
const gnd = circuitJson.find(
  (element) => element.type === "source_net" && element.name === "GND",
)

const inputProblem = convertCircuitJsonToInputProblem(circuitJson, {
  layer: "top",
  subcircuit_id: gnd.subcircuit_id,
  subcircuit_connectivity_map_key: gnd.subcircuit_connectivity_map_key,
  pad_margin: 0.4,
  trace_margin: 0.2,
})
```

Pass `subcircuit_id` when selecting a net inside a subcircuit. The converter
considers that subcircuit and its child subcircuits, but it does not treat
matching child `subcircuit_connectivity_map_key` values as connected unless the
Circuit JSON connectivity actually connects them. Internally, the generated
`globalConnectivityMap` is kept separate from the scoped
`subcircuitConnectivityMap`; scoped solver connectivity keys are prefixed with
their subcircuit id.

Do not generate or pass ids from `circuit-json-to-connectivity-map`. The
converter handles PCB connectivity internally and normalizes it to stable
`subcircuit_connectivity_map_key` values.

## Manual Input

You can skip Circuit JSON conversion and provide the solver input directly.

```ts
import {
  CopperPourPipelineSolver,
  initializeManifoldGeometry,
  type InputProblem,
} from "@tscircuit/copper-pour-solver"

await initializeManifoldGeometry()

const input: InputProblem = {
  regionsForPour: [
    {
      shape: "rect",
      layer: "top",
      bounds: { minX: -10, minY: -5, maxX: 10, maxY: 5 },
      connectivityKey: "net:GND",
      padMargin: 0.4,
      traceMargin: 0.2,
      pourMargin: 0.2,
      board_edge_margin: 0.1,
      use_thermal_reliefs: true,
      thermal_relief_spoke_width: 0.3,
      thermal_relief_spoke_count: 4,
    },
  ],
  pads: [
    {
      shape: "circle",
      padId: "via_1",
      layer: "top",
      connectivityKey: "net:VCC",
      x: 0,
      y: 0,
      radius: 0.5,
      isPlatedHole: true,
    },
  ],
}

const output = new CopperPourPipelineSolver(input).getOutput()
```

Supported input pad shapes are `rect`, `circle`, `pill`, `trace`, and `polygon`.
Use the same `connectivityKey` as the pour for pads/traces that should connect to
the copper island; use a different key for blockers that should be cleared.
When multiple regions share a layer, later entries have higher priority. A
lower-priority region is cleared from higher-priority, different-net copper by
the larger of the two regions' `pourMargin` values. Same-net regions and regions
on different layers do not block each other.

## Output

`getOutput()` returns:

```ts
interface PipelineOutput {
  brep_shapes: BRepShape[]
  brep_shapes_by_region: BRepShape[][]
}
```

`brep_shapes` is the flattened result in input-region order.
`brep_shapes_by_region` preserves the association between each input region and
its generated shapes.

Each B-Rep shape is compatible with Circuit JSON copper pour data:

```ts
interface BRepShape {
  outer_ring: {
    vertices: Array<{ x: number; y: number; bulge?: number }>
  }
  inner_rings: Array<{
    vertices: Array<{ x: number; y: number; bulge?: number }>
  }>
}
```

## Development

```bash
bun install
bun run build
bun test
bun start
bun run build:site
```
