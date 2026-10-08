# @tscircuit/krt-wasm

This package is a fork of [KiCad Routing Tools](https://github.com/drandyhaas/KiCadRoutingTools) adapted to run inside tscircuit as a custom autorouter.

The fork keeps the upstream Rust `GridRouter` core and exposes it through WASM for tscircuit's `autorouter={{ algorithmFn }}` API. It is not a full port of the KiCad plugin or the Python command-line tools.

## Install

```bash
bun add @tscircuit/krt-wasm
```

## Usage

```tsx
import { createKiCadRoutingToolsAutorouter } from "@tscircuit/krt-wasm"

export default () => (
  <board
    width="34mm"
    height="22mm"
    layers={4}
    autorouter={{
      algorithmFn: createKiCadRoutingToolsAutorouter({
        gridStep: 0.1,
        clearance: 0.2,
        maxIterations: 300_000,
        busAttractionRadius: 1,
        busAttractionBonus: 5_000,
      }),
    }}
  >
    {/* components and traces */}
  </board>
)
```

## Build

```bash
bun install
bun run build
```

`bun run build` compiles the Rust router to WASM with `wasm-pack`, embeds the WASM loader in `pkg-node`, and builds the TypeScript wrapper in `dist`.

## What Is Implemented

- tscircuit `GenericLocalAutorouter` wrapper with `complete`, `error`, and `progress` events.
- `SimpleRouteJson` input support from tscircuit.
- Sequential routing of each tscircuit connection using the KRT Rust `GridRouter::route_multi` core.
- Multi-layer routing with via insertion.
- Layer names for `top`, `bottom`, and generated `innerN` layers.
- KiCad-style layer aliases for `F.Cu` and `B.Cu` in input layer fields.
- Rectangular obstacle avoidance from `SimpleRouteJson.obstacles`.
- Obstacle layer filtering through `layer` or `layers`.
- Connected-obstacle exemptions for the net currently being routed.
- Routing bounds enforcement from `SimpleRouteJson.bounds`.
- Trace width selection from connection width, nominal width, or input defaults.
- Configurable grid step, clearance, max iterations, via cost, heuristic weight, turn cost, layer costs, layer direction preferences, and track margin.
- Explicit `SimpleRouteJson.buses` routing, with physical lane ordering, center-out routing, and neighbor-path attraction.
- Basic post-processing to compact collinear route points and collapse very short same-layer tunnels.

## What Is Not Implemented

These upstream KiCad Routing Tools features are not available through this npm/WASM package:

- KiCad PCB parsing or writing.
- KiCad action plugin UI.
- Python CLI workflows such as `route.py`, `route_diff.py`, `route_planes.py`, and `route_disconnected_planes.py`.
- Differential-pair routing orchestration.
- Length matching, time matching, trombone generation, or impedance-controlled width calculation.
- BGA/QFN fanout generation.
- Power/ground plane generation, plane via stitching, plane region repair, or return-via placement.
- Rip-up and reroute orchestration across failed nets.
- MPS net ordering, target swap optimization, polarity swapping, or schematic synchronization.
- Curved, polygonal, circular, or rotated obstacle geometry beyond the rectangular obstacle approximation consumed from `SimpleRouteJson`.
- KiCad net classes, design-rule extraction, DRC reporting, or KiCad board-edge geometry parsing.

## Relationship To Upstream

Upstream KiCad Routing Tools is a KiCad-focused project with a Rust router, Python orchestration, KiCad file IO, and a KiCad plugin. This fork packages the Rust grid-router path for tscircuit so that tscircuit boards can call it directly during autorouting.

For KiCad usage, plugin installation, Python CLI commands, and the complete upstream feature set, use the original repository: https://github.com/drandyhaas/KiCadRoutingTools
