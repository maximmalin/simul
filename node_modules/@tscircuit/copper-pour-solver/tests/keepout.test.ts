import { expect, test } from "bun:test"
import type { AnyCircuitElement } from "circuit-json"
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
} from "lib/index"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

const circuitJson = [
  {
    type: "source_net",
    source_net_id: "source_net_gnd",
    name: "GND",
    member_source_group_ids: [],
    is_ground: true,
    is_power: false,
    is_positive_voltage_source: false,
    subcircuit_id: "subcircuit_0",
    subcircuit_connectivity_map_key: "subcircuit_0_connectivity_gnd",
  },
  {
    type: "pcb_board",
    pcb_board_id: "pcb_board_0",
    center: { x: 0, y: 0 },
    thickness: 1.6,
    num_layers: 2,
    width: 10,
    height: 10,
    material: "fr4",
  },
  {
    type: "pcb_keepout",
    pcb_keepout_id: "pcb_keepout_rect",
    shape: "rect",
    center: { x: -2, y: 0 },
    width: 2,
    height: 3,
    layers: ["top"],
  },
  {
    type: "pcb_keepout",
    pcb_keepout_id: "pcb_keepout_circle",
    shape: "circle",
    center: { x: 2, y: 0 },
    radius: 1,
    layers: ["top"],
  },
  {
    type: "pcb_keepout",
    pcb_keepout_id: "pcb_keepout_bottom_only",
    shape: "circle",
    center: { x: 0, y: 3 },
    radius: 0.5,
    layers: ["bottom"],
  },
] as AnyCircuitElement[]

test("copper pours are clipped around keepouts on their layer", async () => {
  const inputProblem = convertCircuitJsonToInputProblem(circuitJson, {
    layer: "top",
    source_net_id: "source_net_gnd",
    pad_margin: 0.2,
    trace_margin: 0.1,
  })

  const keepoutPads = inputProblem.pads.filter((pad) =>
    pad.connectivityKey.startsWith("keepout:"),
  )
  expect(keepoutPads.map((pad) => pad.padId).sort()).toEqual([
    "pcb_keepout_circle",
    "pcb_keepout_rect",
  ])

  const output = new CopperPourPipelineSolver(inputProblem).getOutput()
  expect(output.brep_shapes).toHaveLength(1)
  expect(output.brep_shapes[0]!.inner_rings).toHaveLength(2)

  const svg = runSolverAndRenderToSvg(circuitJson, {
    layer: "top",
    net_name: "GND",
    pad_margin: 0.2,
    trace_margin: 0.1,
  })
  await expect(svg).toMatchSvgSnapshot(import.meta.path, "keepout")
})
