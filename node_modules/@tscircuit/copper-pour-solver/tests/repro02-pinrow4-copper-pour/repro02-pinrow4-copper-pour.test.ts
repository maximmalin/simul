import { expect, test } from "bun:test"
import type { AnyCircuitElement } from "circuit-json"
import circuitJson from "./repro02-pinrow4-copper-pour.json"
import { runSolverAndRenderToSvg } from "../utils/run-solver-and-render-to-svg"

test("repro02-pinrow4-copper-pour", async () => {
  const svg = runSolverAndRenderToSvg(circuitJson as AnyCircuitElement[], {
    layer: "top",
    net_name: "GND",
    pad_margin: 0.2,
    trace_margin: 0.2,
  })

  await expect(svg).toMatchSvgSnapshot(
    import.meta.path,
    "repro02-pinrow4-copper-pour",
  )
})
