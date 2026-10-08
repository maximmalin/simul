# @tscircuit/pcb-trace-linter

A solver pipeline for aesthetic analysis of routed PCB traces. Accepts SimpleRouteJson or Circuit JSON and returns located issues without changing routes.

```sh
bun add github:tscircuit/pcb-trace-linter
```

```ts
import { PcbTraceLinter } from "@tscircuit/pcb-trace-linter"

const solver = new PcbTraceLinter({
  input: simpleRouteJson, // or a Circuit JSON array
  angleToleranceDegrees: 0.1,
})
solver.solve()
if (solver.failed) throw new Error(solver.error ?? "Lint failed")
const issues = solver.getOutput()
const graphics = solver.finalVisualize()
```

Each `odd_angle` issue includes the trace ID and index, original route segment indices, layer, start/end coordinates, midpoint `location`, absolute direction in degrees, nearest allowed direction, and angular deviation. Coordinates and lengths are in millimeters. IDs are deterministic for a given input order.

## Rule

`OddAngleFinder` permits directions at multiples of 45° (including horizontal/vertical) within a configurable 0.1° tolerance. It inspects each straight wire segment, including wires entering/leaving a via. Duplicate points and segments shorter than `minSegmentLength` (default 1e-9 mm) are ignored. Vertical layer transitions, jumper bodies, and through-obstacle transitions are not planar wire segments. This is a segment-direction check, not a clearance or electrical DRC check.

Circuit JSON uses `getSimpleRouteJsonFromCircuitJson` from `@tscircuit/core` for the board, connectivity, and obstacles. Traces are set aside before conversion (core's obstacle conversion rejects some diagonal standalone traces) and restored as SRJ routes for linting, including copper that core would exclude from a routing problem. Unsupported Circuit JSON route types such as teardrops currently produce an explicit error. Uploads must contain already routed copper; the linter does not autoroute uploads.

## Solver architecture

`PcbTraceLinter` extends `BasePipelineSolver` from `@tscircuit/solver-utils`. Its first and only stage is `OddAngleFinder`, a `BaseSolver` that advances one segment at a time. `getOutput()` aggregates stage issue lists; each stage also supplies debugger graphics and constructor parameters. Add future identifiers as separate `definePipelineStep` entries. No spatial index is needed for this linear scan.

## Develop

```sh
bun install
bun run start          # Cosmos, welcome/upload page and dataset debugger
bun test               # unit, conversion, issue-list and visual snapshots
bun run typecheck
bun run format:check
bun run build:site     # static Cosmos export
```

Both Cosmos pages use `GenericSolverDebugger`. `_welcome.page.tsx` accepts a Circuit JSON array or SRJ object, supports stepping/animation/solving, shows numbered issue locations, and exports the issue list as JSON. Loading another file creates a fresh debugger session. The bundled routed example loads initially.

If macOS has exhausted its filesystem watcher limit, use `CHOKIDAR_USEPOLLING=true bun run start`.

## Pipeline9 fixture

The committed full-board fixture is dataset-srj18 `sample003` (Arduino Micro), selected from the dataset used by `tscircuit/tscircuit-autorouter`. It was routed with `AutoroutingPipelineSolver9_PreloadedTraceGraph` from `@tscircuit/capacity-autorouter@0.0.928`, with default options: 103 input connections, 145 output traces, and 320 odd-angle segments at the default tolerance.

The original input and routed output are committed under `tests/fixtures`. Regenerate the routed output with:

```sh
bun run generate:fixture
BUN_UPDATE_SNAPSHOTS=1 bun test -u
```

See [fixture provenance](tests/fixtures/README.md) for pinned sources. Visual snapshots include a full-board overview and a labeled detail for every issue. Detail drawings preserve segment direction but normalize display size; endpoint labels retain real coordinates.

Bootstrapped according to the [tscircuit handbook](https://github.com/tscircuit/handbook/blob/main/guides/bootstrapping-repos.md), using source installation (`module: lib/index.ts`), no lockfile, Biome, Bun checks, and Cosmos. Repository provisioned through [tscircuit/create-repo#76](https://github.com/tscircuit/create-repo/pull/76).

SRJ consumers such as the autorouter can import `PcbTraceLinter` and `validateSimpleRouteJson` from `@tscircuit/pcb-trace-linter/srj`. This entry does not load core's Circuit JSON renderer or a React runtime, so it also works in React 18 applications. Use the main entry for Circuit JSON conversion.
