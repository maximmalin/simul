import { expect, test } from "bun:test"
import circuitJson from "./assets/polygon-board-2.json"
import type { AnyCircuitElement } from "circuit-json"
import { runSolverAndRenderToSvg } from "tests/utils/run-solver-and-render-to-svg"

test("polygon board 2", async () => {
  const svg = runSolverAndRenderToSvg(circuitJson as AnyCircuitElement[], {
    layer: "top",
    net_name: "GND",
    pad_margin: 0.2,
    trace_margin: 0.1,
    board_edge_margin: 0.2,
  })

  await expect(svg).toMatchSvgSnapshot(import.meta.path, "polygon-board-2")
})
