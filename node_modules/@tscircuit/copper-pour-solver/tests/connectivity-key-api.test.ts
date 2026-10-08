import { expect, test } from "bun:test"
import type { AnyCircuitElement } from "circuit-json"
import { getFullConnectivityMapFromCircuitJson } from "circuit-json-to-connectivity-map"
import { convertCircuitJsonToInputProblem } from "lib/circuit-json/convert-circuit-json-to-input-problem"
import subcircuitConnectivityScopeCircuitJson from "./assets/subcircuit-connectivity-scope.json"

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
    subcircuit_connectivity_map_key: "stable_subcircuit_net_gnd",
  },
  {
    type: "source_trace",
    source_trace_id: "source_trace_gnd",
    connected_source_net_ids: ["source_net_gnd"],
    connected_source_port_ids: ["source_port_gnd"],
    subcircuit_connectivity_map_key: "stable_subcircuit_net_gnd",
  },
  {
    type: "source_port",
    source_port_id: "source_port_gnd",
    source_component_id: "source_component_r1",
    name: "1",
    subcircuit_connectivity_map_key: "stable_subcircuit_net_gnd",
  },
  {
    type: "pcb_port",
    pcb_port_id: "pcb_port_gnd",
    pcb_component_id: "pcb_component_r1",
    source_port_id: "source_port_gnd",
    x: 0,
    y: 0,
    layers: ["top"],
  },
  {
    type: "pcb_smtpad",
    pcb_smtpad_id: "pcb_smtpad_gnd",
    pcb_component_id: "pcb_component_r1",
    pcb_port_id: "pcb_port_gnd",
    layer: "top",
    shape: "rect",
    x: 0,
    y: 0,
    width: 1,
    height: 1,
    port_hints: ["1"],
  },
] as AnyCircuitElement[]

test("circuit-json adapter resolves pours to subcircuit connectivity keys", () => {
  const connectivityMap = getFullConnectivityMapFromCircuitJson(circuitJson)
  const generatedConnectivityMapKey =
    connectivityMap.getNetConnectedToId("source_net_gnd")

  expect(generatedConnectivityMapKey).toBeDefined()
  expect(generatedConnectivityMapKey).not.toBe("stable_subcircuit_net_gnd")

  const inputProblem = convertCircuitJsonToInputProblem(circuitJson, {
    layer: "top",
    source_net_id: "source_net_gnd",
    pad_margin: 0.2,
    trace_margin: 0.2,
  })

  expect(inputProblem.regionsForPour[0]?.connectivityKey).toBe(
    "stable_subcircuit_net_gnd",
  )
  expect(
    inputProblem.pads.find((pad) => pad.padId === "pcb_smtpad_gnd"),
  ).toMatchObject({
    connectivityKey: "stable_subcircuit_net_gnd",
  })
})

test("circuit-json adapter rejects generated connectivity map keys", () => {
  const generatedConnectivityMapKey =
    getFullConnectivityMapFromCircuitJson(circuitJson).getNetConnectedToId(
      "source_net_gnd",
    )

  expect(() =>
    convertCircuitJsonToInputProblem(circuitJson, {
      layer: "top",
      pour_connectivity_key: generatedConnectivityMapKey!,
      pad_margin: 0.2,
      trace_margin: 0.2,
    }),
  ).toThrow(/subcircuit_connectivity_map_key/)
})

test("subcircuit_id scopes repeated subcircuit connectivity keys", () => {
  const inputProblem = convertCircuitJsonToInputProblem(
    subcircuitConnectivityScopeCircuitJson as AnyCircuitElement[],
    {
      layer: "top",
      subcircuit_id: "subcircuit_child_a",
      subcircuit_connectivity_map_key: "net0",
      pad_margin: 0.2,
      trace_margin: 0.2,
    },
  )

  const childAPad = inputProblem.pads.find(
    (pad) => pad.padId === "pcb_smtpad_child_a_gnd",
  )
  const childBPad = inputProblem.pads.find(
    (pad) => pad.padId === "pcb_smtpad_child_b_gnd",
  )

  expect(inputProblem.regionsForPour[0]?.connectivityKey).toBe(
    "subcircuit:subcircuit_child_a:connectivity:net0",
  )
  expect(childAPad?.connectivityKey).toBe(
    inputProblem.regionsForPour[0]?.connectivityKey,
  )
  expect(childBPad?.connectivityKey).not.toBe(
    inputProblem.regionsForPour[0]?.connectivityKey,
  )
})

test("parent subcircuit selection rejects ambiguous child connectivity keys", () => {
  expect(() =>
    convertCircuitJsonToInputProblem(
      subcircuitConnectivityScopeCircuitJson as AnyCircuitElement[],
      {
        layer: "top",
        subcircuit_id: "subcircuit_parent",
        subcircuit_connectivity_map_key: "net0",
        pad_margin: 0.2,
        trace_margin: 0.2,
      },
    ),
  ).toThrow(/multiple subcircuits/)
})
