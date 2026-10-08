import { expect, test } from "bun:test"
import type { AnyCircuitElement } from "circuit-json"
import { convertCircuitJsonToInputProblem } from "lib"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

const smtPadCases = [
  {
    label: "RECT",
    x: -6,
    pad: { shape: "rect", x: -6, y: 0, width: 1.8, height: 2.6 },
  },
  {
    label: "ROTATED RECT",
    x: -3,
    pad: {
      shape: "rotated_rect",
      x: -3,
      y: 0,
      width: 1.8,
      height: 2.6,
      ccw_rotation: 30,
    },
  },
  {
    label: "CIRCLE",
    x: 0,
    pad: { shape: "circle", x: 0, y: 0, radius: 0.9 },
  },
  {
    label: "ROTATED PILL",
    x: 3,
    pad: {
      shape: "rotated_pill",
      x: 3,
      y: 0,
      width: 1.4,
      height: 2.6,
      radius: 0.7,
      ccw_rotation: -35,
    },
  },
  {
    label: "POLYGON",
    x: 6,
    pad: {
      shape: "polygon",
      points: [
        { x: 5.1, y: -0.9 },
        { x: 6.5, y: -1.15 },
        { x: 6.95, y: 0 },
        { x: 6.35, y: 1.1 },
        { x: 5.15, y: 0.75 },
      ],
    },
  },
] as const

const circuitJson: AnyCircuitElement[] = [
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
    type: "pcb_board",
    pcb_board_id: "pcb_board_0",
    center: { x: 0, y: 0 },
    width: 16,
    height: 6,
    thickness: 1.4,
    num_layers: 2,
    material: "fr4",
  },
  ...smtPadCases.flatMap(({ label, x, pad }, index) => {
    const id = `${index}`
    return [
      {
        type: "source_component",
        source_component_id: `source_component_${id}`,
        ftype: "simple_resistor",
        name: `U${id}`,
        source_group_id: "source_group_0",
      },
      {
        type: "source_port",
        source_port_id: `source_port_${id}`,
        source_component_id: `source_component_${id}`,
        name: "GND",
        pin_number: 1,
        subcircuit_id: "subcircuit_0",
        subcircuit_connectivity_map_key: "net_gnd",
      },
      {
        type: "source_trace",
        source_trace_id: `source_trace_${id}`,
        connected_source_port_ids: [`source_port_${id}`],
        connected_source_net_ids: ["source_net_gnd"],
        subcircuit_id: "subcircuit_0",
        subcircuit_connectivity_map_key: "net_gnd",
      },
      {
        type: "pcb_component",
        pcb_component_id: `pcb_component_${id}`,
        source_component_id: `source_component_${id}`,
        subcircuit_id: "subcircuit_0",
        center: { x, y: 0 },
        width: 2,
        height: 3,
        layer: "top",
        rotation: 0,
      },
      {
        type: "pcb_port",
        pcb_port_id: `pcb_port_${id}`,
        pcb_component_id: `pcb_component_${id}`,
        source_port_id: `source_port_${id}`,
        subcircuit_id: "subcircuit_0",
        x,
        y: 0,
        layers: ["top"],
      },
      {
        type: "pcb_smtpad",
        pcb_smtpad_id: `pcb_smtpad_${id}`,
        pcb_component_id: `pcb_component_${id}`,
        pcb_port_id: `pcb_port_${id}`,
        subcircuit_id: "subcircuit_0",
        layer: "top",
        port_hints: ["GND", "pin1"],
        is_covered_with_solder_mask: false,
        ...pad,
      },
      {
        type: "pcb_note_text",
        pcb_note_text_id: `pcb_note_text_${id}`,
        text: label,
        x,
        y: -2.25,
        font_size: 0.28,
        anchor_position: "center",
        anchor_alignment: "center",
      },
    ]
  }),
] as AnyCircuitElement[]

test("thermal reliefs support every SMT pad shape", async () => {
  const options = {
    layer: "top" as const,
    source_net_id: "source_net_gnd",
    pad_margin: 0.35,
    trace_margin: 0.2,
    board_edge_margin: 0.2,
    use_thermal_reliefs: true,
    thermal_relief_spoke_width: 0.25,
    thermal_relief_spoke_count: 4,
  }
  const inputProblem = convertCircuitJsonToInputProblem(circuitJson, options)

  expect(inputProblem.pads).toHaveLength(smtPadCases.length)
  expect(inputProblem.pads.every((pad) => pad.isSmtPad)).toBe(true)
  expect(inputProblem.pads.map((pad) => pad.shape)).toEqual([
    "rect",
    "rotated_rect",
    "circle",
    "pill",
    "polygon",
  ])

  const svg = runSolverAndRenderToSvg(circuitJson, {
    ...options,
    net_name: "GND",
  })
  await expect(svg).toMatchSvgSnapshot(
    import.meta.path,
    "thermal-relief-all-smtpad-shapes",
  )
})
