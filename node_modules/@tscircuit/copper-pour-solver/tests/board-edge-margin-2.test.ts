import { expect, test } from "bun:test"
import circuitJson from "./assets/board-edge-margin-2.json"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"
import type { AnyCircuitElement } from "circuit-json"

test("board edge margin 2", async () => {
  const svg = runSolverAndRenderToSvg(circuitJson as AnyCircuitElement[], {
    layer: "top",
    net_name: "GND",
    pad_margin: 0.4,
    trace_margin: 0.2,
    board_edge_margin: 1,
  })

  await expect(svg).toMatchSvgSnapshot(import.meta.path, "board-edge-margin-2")
})
