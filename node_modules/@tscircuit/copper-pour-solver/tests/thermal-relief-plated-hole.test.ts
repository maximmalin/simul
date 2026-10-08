import { expect, test } from "bun:test"
import type { AnyCircuitElement } from "circuit-json"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

const createSinglePlatedHoleCircuitJson = (
  platedHoleShape: Record<string, unknown>,
): AnyCircuitElement[] =>
  [
    {
      type: "source_group",
      source_group_id: "source_group_0",
      is_subcircuit: true,
      subcircuit_id: "subcircuit_0",
    },
    {
      type: "source_net",
      source_net_id: "source_net_gnd",
      name: "GND",
      subcircuit_id: "subcircuit_0",
      subcircuit_connectivity_map_key: "net_gnd",
    },
    {
      type: "source_component",
      source_component_id: "source_component_0",
      ftype: "simple_pin_header",
      name: "J1",
      source_group_id: "source_group_0",
    },
    {
      type: "source_port",
      source_port_id: "source_port_gnd",
      source_component_id: "source_component_0",
      name: "GND",
      pin_number: 1,
      subcircuit_id: "subcircuit_0",
      subcircuit_connectivity_map_key: "net_gnd",
    },
    {
      type: "source_trace",
      source_trace_id: "source_trace_gnd",
      connected_source_port_ids: ["source_port_gnd"],
      connected_source_net_ids: ["source_net_gnd"],
      subcircuit_id: "subcircuit_0",
      subcircuit_connectivity_map_key: "net_gnd",
    },
    {
      type: "pcb_component",
      pcb_component_id: "pcb_component_0",
      source_component_id: "source_component_0",
      subcircuit_id: "subcircuit_0",
      center: { x: 0, y: 0 },
      width: 2,
      height: 2,
      layer: "top",
      rotation: 0,
    },
    {
      type: "pcb_port",
      pcb_port_id: "pcb_port_gnd",
      pcb_component_id: "pcb_component_0",
      source_port_id: "source_port_gnd",
      subcircuit_id: "subcircuit_0",
      x: 0,
      y: 0,
      layers: ["top", "bottom"],
    },
    {
      type: "pcb_board",
      pcb_board_id: "pcb_board_0",
      center: { x: 0, y: 0 },
      width: 8,
      height: 8,
      thickness: 1.4,
      num_layers: 2,
      material: "fr4",
    },
    {
      type: "pcb_plated_hole",
      pcb_plated_hole_id: "pcb_plated_hole_gnd",
      pcb_component_id: "pcb_component_0",
      pcb_port_id: "pcb_port_gnd",
      subcircuit_id: "subcircuit_0",
      layers: ["top", "bottom"],
      port_hints: ["GND", "pin1"],
      x: 0,
      y: 0,
      ...platedHoleShape,
    },
  ] as AnyCircuitElement[]

const cases = [
  {
    name: "circle with 4 spokes",
    snapshotName: "thermal-relief-circle-4-spokes",
    platedHoleShape: {
      shape: "circle",
      outer_diameter: 2.4,
      hole_diameter: 1.2,
    },
    thermal_relief_spoke_width: 0.3,
    thermal_relief_spoke_count: 4,
  },
  {
    name: "pill with 3 spokes",
    snapshotName: "thermal-relief-pill-3-spokes",
    platedHoleShape: {
      shape: "pill",
      outer_width: 1.8,
      outer_height: 3,
      hole_width: 1,
      hole_height: 2.2,
      ccw_rotation: 30,
    },
    thermal_relief_spoke_width: 0.25,
    thermal_relief_spoke_count: 3,
  },
  {
    name: "rotated oval with 4 spokes",
    snapshotName: "thermal-relief-oval-4-spokes",
    platedHoleShape: {
      shape: "oval",
      outer_width: 1.8,
      outer_height: 3,
      hole_width: 1,
      hole_height: 1.4,
      ccw_rotation: 30,
    },
    thermal_relief_spoke_width: 0.25,
    thermal_relief_spoke_count: 4,
  },
  {
    name: "rotated rectangular pad with 6 spokes",
    snapshotName: "thermal-relief-rect-pad-6-spokes",
    platedHoleShape: {
      shape: "circular_hole_with_rect_pad",
      hole_shape: "circle",
      pad_shape: "rect",
      hole_diameter: 1,
      rect_pad_width: 2.6,
      rect_pad_height: 1.8,
      rect_ccw_rotation: 25,
      hole_offset_x: 0,
      hole_offset_y: 0,
    },
    thermal_relief_spoke_width: 0.18,
    thermal_relief_spoke_count: 6,
  },
] as const

for (const thermalCase of cases) {
  test(`thermal relief around a ${thermalCase.name}`, async () => {
    const svg = runSolverAndRenderToSvg(
      createSinglePlatedHoleCircuitJson(thermalCase.platedHoleShape),
      {
        layer: "top",
        net_name: "GND",
        pad_margin: 0.35,
        trace_margin: 0.2,
        board_edge_margin: 0.2,
        use_thermal_reliefs: true,
        thermal_relief_spoke_width: thermalCase.thermal_relief_spoke_width,
        thermal_relief_spoke_count: thermalCase.thermal_relief_spoke_count,
      },
    )

    await expect(svg).toMatchSvgSnapshot(
      import.meta.path,
      thermalCase.snapshotName,
    )
  })
}
