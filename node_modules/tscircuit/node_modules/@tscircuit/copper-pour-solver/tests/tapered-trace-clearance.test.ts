import { convertCircuitJsonToPcbSvg } from "circuit-to-svg-tapers"
import { expect, test } from "bun:test"
import type { AnyCircuitElement, PcbTrace } from "circuit-json"
import { convertCircuitJsonToInputProblem } from "lib/index"
import { processObstaclesForPour } from "lib/solvers/copper-pour/process-obstacles"
import type { Point } from "@tscircuit/math-utils"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

// Board-world positions in mm, +X right and +Y up.
const contains = (polygon: Point[], point: Point) => {
  let inside = false
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i]!
    const b = polygon[j]!
    if (
      a.y > point.y !== b.y > point.y &&
      point.x < ((b.x - a.x) * (point.y - a.y)) / (b.y - a.y) + a.x
    )
      inside = !inside
  }
  return inside
}

const circuitJson: AnyCircuitElement[] = [
  {
    type: "pcb_board",
    pcb_board_id: "board",
    center: { x: 0, y: 0 },
    width: 12,
    height: 9,
    thickness: 1.6,
    num_layers: 2,
    material: "fr4",
  },
  {
    type: "source_net",
    source_net_id: "gnd",
    member_source_group_ids: [],
    name: "GND",
    subcircuit_connectivity_map_key: "gnd",
  },
]
for (const [index, mode] of (["quadratic", "linear"] as const).entries()) {
  const y = index === 0 ? 1.8 : -1.8
  circuitJson.push(
    {
      type: "source_trace",
      source_trace_id: `signal${index}`,
      connected_source_net_ids: [],
      connected_source_port_ids: [],
      subcircuit_connectivity_map_key: `signal${index}`,
    },
    {
      type: "pcb_trace",
      pcb_trace_id: `trace${index}`,
      source_trace_id: `signal${index}`,
      subcircuit_connectivity_map_key: `signal${index}`,
      route: [
        {
          route_type: "wire",
          x: -3,
          y,
          width: 1,
          start_width: 1,
          end_width: 0.2,
          width_interpolation_mode: mode,
          layer: "top",
        },
        { route_type: "wire", x: -1, y, width: 0.2, layer: "top" },
        { route_type: "wire", x: 2, y, width: 0.2, layer: "top" },
      ],
    } as PcbTrace,
    {
      type: "pcb_note_text",
      pcb_note_text_id: `label${index}`,
      text: `${mode}: pour follows taper`,
      anchor_position: { x: 0, y: y + 0.9 },
      anchor_alignment: "center",
      font: "tscircuit2024",
      font_size: 0.3,
      color: "#a8d8ff",
    } as AnyCircuitElement,
  )
}

const options = {
  layer: "top" as const,
  source_net_id: "gnd",
  pad_margin: 0.8,
  trace_margin: 0.1,
}

test("native taper clearance follows the profile with trace margin and net/layer isolation", async () => {
  const original = JSON.stringify(circuitJson)
  const input = convertCircuitJsonToInputProblem(circuitJson, options)
  expect(input.pads.filter((p) => p.shape === "tapered_trace")).toHaveLength(2)
  const { polygonsToSubtract } = processObstaclesForPour(input.pads, "gnd", {
    padMargin: 0.8,
    traceMargin: 0.1,
  })
  const blocked = (x: number, y: number) =>
    polygonsToSubtract.some((p) => contains(p, { x, y }))
  expect(blocked(-2.8, 2.2)).toBe(true)
  // Clear at the narrow end: a maximum-width envelope would incorrectly block it.
  expect(blocked(-1.2, 2.2)).toBe(false)
  expect(blocked(0, 1.95)).toBe(true)
  expect(blocked(0, 2.15)).toBe(false)
  expect(
    convertCircuitJsonToInputProblem(circuitJson, {
      ...options,
      layer: "bottom",
    }).pads,
  ).toHaveLength(0)
  expect(
    processObstaclesForPour(
      input.pads.filter((p) => p.connectivityKey === "signal0"),
      "signal0",
      { padMargin: 0.8, traceMargin: 0.1 },
    ).polygonsToSubtract,
  ).toHaveLength(0)
  expect(JSON.stringify(circuitJson)).toBe(original)
  const svg = runSolverAndRenderToSvg(
    circuitJson,
    {
      ...options,
      net_name: "GND",
    },
    convertCircuitJsonToPcbSvg,
  )
  await expect(svg).toMatchSvgSnapshot(import.meta.path)
})

test("reversed tapers and via endpoints preserve layer-specific obstacles", () => {
  const trace: PcbTrace = {
    type: "pcb_trace",
    pcb_trace_id: "via_trace",
    source_trace_id: "signal0",
    route: [
      {
        route_type: "wire",
        x: 2,
        y: 0,
        layer: "top",
        width: 0.2,
        start_width: 0.2,
        end_width: 1,
        width_interpolation_mode: "quadratic",
      },
      { route_type: "via", x: 0, y: 0, from_layer: "top", to_layer: "bottom" },
      { route_type: "wire", x: 0, y: 0, layer: "bottom", width: 0.2 },
      { route_type: "wire", x: -2, y: 0, layer: "bottom", width: 0.2 },
    ],
  }
  const input: AnyCircuitElement[] = [
    ...circuitJson.filter((e) => e.type !== "pcb_trace"),
    trace,
  ]
  const top = convertCircuitJsonToInputProblem(input, options).pads
  expect(top).toHaveLength(1)
  expect(top[0]).toMatchObject({
    shape: "tapered_trace",
    start: { x: 2, y: 0 },
    end: { x: 0, y: 0 },
    start_width: 0.2,
    end_width: 1,
  })
  const { polygonsToSubtract } = processObstaclesForPour(top, "gnd", {
    padMargin: 0.8,
    traceMargin: 0.1,
  })
  expect(polygonsToSubtract.some((p) => contains(p, { x: 0.2, y: 0.4 }))).toBe(
    true,
  )
  expect(polygonsToSubtract.some((p) => contains(p, { x: 1.8, y: 0.4 }))).toBe(
    false,
  )
  const bottom = convertCircuitJsonToInputProblem(input, {
    ...options,
    layer: "bottom",
  }).pads
  expect(bottom).toHaveLength(1)
  expect(bottom[0]).toMatchObject({
    shape: "trace",
    width: 0.2,
    segments: [
      { x: 0, y: 0 },
      { x: -2, y: 0 },
    ],
  })
})
