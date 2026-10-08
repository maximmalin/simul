import { expect, test } from "bun:test"
import circuitJson from "./assets/polygon-board.json"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

test("board edge margin", async () => {
  const svg = runSolverAndRenderToSvg(circuitJson as any, {
    layer: "top",
    net_name: "GND",
    pad_margin: 0.4,
    trace_margin: 0.2,
    board_edge_margin: 1,
  })

  await expect(svg).toMatchSvgSnapshot(import.meta.path, "board-edge-margin")
})
