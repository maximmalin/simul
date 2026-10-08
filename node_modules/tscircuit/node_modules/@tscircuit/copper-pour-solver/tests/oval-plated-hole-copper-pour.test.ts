import { expect, test } from "bun:test"
import type { AnyCircuitElement } from "circuit-json"
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
} from "lib/index"

const circuitJson = [
  {
    type: "source_net",
    source_net_id: "source_net_gnd",
    name: "GND",
    subcircuit_connectivity_map_key: "net_gnd",
    member_source_group_ids: [],
    is_ground: true,
    is_power: false,
    is_positive_voltage_source: false,
  },
  {
    type: "pcb_board",
    pcb_board_id: "pcb_board_0",
    center: { x: 0, y: 0 },
    width: 8,
    height: 8,
    thickness: 1.6,
    num_layers: 2,
    material: "fr4",
  },
  {
    type: "pcb_plated_hole",
    pcb_plated_hole_id: "pcb_plated_hole_oval",
    pcb_component_id: "pcb_component_oval",
    shape: "oval",
    x: 0,
    y: 0,
    outer_width: 2,
    outer_height: 3,
    hole_width: 1,
    hole_height: 1.5,
    ccw_rotation: 30,
    layers: ["top", "bottom"],
    port_hints: ["pin1"],
    is_covered_with_solder_mask: false,
  },
] as AnyCircuitElement[]

test("oval plated holes clear copper pour with rotation and pad margin", () => {
  const inputProblem = convertCircuitJsonToInputProblem(circuitJson, {
    layer: "top",
    source_net_id: "source_net_gnd",
    pad_margin: 0.2,
    trace_margin: 0.2,
  })

  expect(inputProblem.pads).toHaveLength(1)
  expect(inputProblem.pads[0]).toMatchObject({
    shape: "oval",
    padId: "pcb_plated_hole_oval",
    isPlatedHole: true,
    x: 0,
    y: 0,
    width: 2,
    height: 3,
    ccwRotation: 30,
  })

  const output = new CopperPourPipelineSolver(inputProblem).getOutput()

  expect(output.brep_shapes).toHaveLength(1)
  expect(output.brep_shapes[0]!.inner_rings).toHaveLength(1)

  const vertices = output.brep_shapes[0]!.inner_rings[0]!.vertices
  const xs = vertices.map(({ x }) => x)
  const ys = vertices.map(({ y }) => y)
  const clearanceWidth = Math.max(...xs) - Math.min(...xs)
  const clearanceHeight = Math.max(...ys) - Math.min(...ys)

  // A 30-degree rotation swaps neither axis and produces distinct extents.
  // Both extents also include the configured 0.2 mm clearance on each side.
  expect(clearanceWidth).toBeCloseTo(2.69, 1)
  expect(clearanceHeight).toBeCloseTo(3.18, 1)
})
