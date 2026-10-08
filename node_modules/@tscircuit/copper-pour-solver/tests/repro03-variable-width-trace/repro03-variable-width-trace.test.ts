import { expect, test } from "bun:test"
import type { AnyCircuitElement } from "circuit-json"
import { convertCircuitJsonToInputProblem } from "lib/circuit-json/convert-circuit-json-to-input-problem"
import type { InputTracePad } from "lib/types"

const circuitJson = [
  {
    type: "pcb_board",
    pcb_board_id: "pcb_board_0",
    center: { x: 0, y: 0 },
    width: 10,
    height: 10,
    thickness: 1.4,
    num_layers: 2,
    material: "fr4",
  },
  {
    type: "source_net",
    source_net_id: "source_net_gnd",
    name: "GND",
    subcircuit_connectivity_map_key: "net_gnd",
  },
  {
    type: "source_net",
    source_net_id: "source_net_signal",
    name: "SIGNAL",
    subcircuit_connectivity_map_key: "net_signal",
  },
  {
    type: "source_trace",
    source_trace_id: "source_trace_signal",
    connected_source_net_ids: ["source_net_signal"],
    connected_source_port_ids: [],
    subcircuit_connectivity_map_key: "net_signal",
  },
  {
    type: "pcb_trace",
    pcb_trace_id: "pcb_trace_signal",
    source_trace_id: "source_trace_signal",
    route: [
      {
        route_type: "wire",
        x: -2,
        y: 0,
        width: 0.4,
        layer: "top",
      },
      {
        route_type: "wire",
        x: 0,
        y: 0,
        width: 1,
        layer: "top",
      },
      {
        route_type: "wire",
        x: 2,
        y: 0,
        width: 1,
        layer: "top",
      },
    ],
  },
] as AnyCircuitElement[]

test("repro03 preserves widths when converting a variable-width trace", () => {
  const inputProblem = convertCircuitJsonToInputProblem(circuitJson, {
    layer: "top",
    source_net_id: "source_net_gnd",
    pad_margin: 0.2,
    trace_margin: 0.2,
  })

  const tracePads = inputProblem.pads.filter(
    (pad): pad is InputTracePad => pad.shape === "trace",
  )

  expect(tracePads.map(({ width, segments }) => ({ width, segments }))).toEqual(
    [
      {
        width: 0.4,
        segments: [
          { x: -2, y: 0 },
          { x: 0, y: 0 },
        ],
      },
      {
        width: 1,
        segments: [
          { x: 0, y: 0 },
          { x: 2, y: 0 },
        ],
      },
    ],
  )
})
