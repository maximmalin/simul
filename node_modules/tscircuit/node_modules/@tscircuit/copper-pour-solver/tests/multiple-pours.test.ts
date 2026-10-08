import { expect, test } from "bun:test"
import type { AnyCircuitElement } from "circuit-json"
import circuitJson from "./assets/multiple-pours.json"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

test("multiple copper pours with complex outlines", async () => {
  const svg = runSolverAndRenderToSvg(circuitJson as AnyCircuitElement[], [
    {
      layer: "top",
      net_name: "GND",
      pad_margin: 0.2,
      trace_margin: 0.2,
      outline: [
        { x: -5, y: -5 },
        { x: -4, y: -5 },
        { x: -3, y: -4 },
        { x: 0, y: -2 },
        { x: -1, y: 3 },
        { x: -5, y: 3 },
        { x: -5, y: -3 },
        { x: -5, y: -5 },
      ],
    },
    {
      layer: "top",
      net_name: "VCC",
      pad_margin: 0.2,
      trace_margin: 0.2,
      outline: [
        { x: 3, y: -5 },
        { x: 5, y: -5 },
        { x: 5, y: 3 },
        { x: 1, y: 3 },
        { x: 0, y: -2 },
        { x: 3, y: -4 },
        { x: 3, y: -5 },
      ],
    },
  ])

  await expect(svg).toMatchSvgSnapshot(import.meta.path, "multiple-pours")
})
