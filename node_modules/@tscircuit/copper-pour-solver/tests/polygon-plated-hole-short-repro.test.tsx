import { expect, test } from "bun:test"
import { Circuit } from "@tscircuit/core"
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
} from "lib/index"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

test("polygon and rectangular plated holes receive ground pour clearance", async () => {
  const clearance = 0.3
  const circuit = new Circuit()
  circuit.add(
    <board width={16} height={10} routingDisabled>
      <net name="GND" isGroundNet />
      <net name="VCC" isPowerNet />
      <chip
        name="J1"
        connections={{ pin1: "net.VCC", pin2: "net.VCC" }}
        footprint={
          <footprint>
            <platedhole
              shape="hole_with_polygon_pad"
              holeShape="circle"
              holeDiameter={0.8}
              holeOffsetX={0}
              holeOffsetY={0}
              pcbX={-3}
              portHints={["pin1"]}
              padOutline={[
                { x: -1, y: -1 },
                { x: 1, y: -1 },
                { x: 1, y: 1 },
                { x: -1, y: 1 },
              ]}
            />
            <platedhole
              shape="circular_hole_with_rect_pad"
              holeDiameter={0.8}
              rectPadWidth={2}
              rectPadHeight={2}
              pcbX={3}
              portHints={["pin2"]}
            />
          </footprint>
        }
      />
      <pcbnotetext
        text="GND POUR: 0.3 mm clearance"
        pcbY={3.5}
        fontSize={0.6}
      />
      <pcbnotetext text="POLYGON: VCC" pcbX={-3} pcbY={2} fontSize={0.5} />
      <pcbnotetext text="RECT: VCC" pcbX={3} pcbY={2} fontSize={0.5} />
      <pcbnotetext
        text="Expected: clearance around BOTH pads"
        pcbY={-3}
        fontSize={0.5}
      />
    </board>,
  )
  await circuit.renderUntilSettled()
  const circuitJson = circuit.getCircuitJson()
  const platedHoles = circuitJson.filter(
    (elm) => elm.type === "pcb_plated_hole",
  )
  expect(platedHoles).toHaveLength(2)
  const polygonPad = platedHoles.find(
    (pad) => pad.shape === "hole_with_polygon_pad",
  )!
  const rectPad = platedHoles.find(
    (pad) => pad.shape === "circular_hole_with_rect_pad",
  )!
  const inputProblem = convertCircuitJsonToInputProblem(circuitJson, {
    layer: "bottom",
    source_net_name: "GND",
    pad_margin: clearance,
    trace_margin: clearance,
    board_edge_margin: 0.5,
  })

  expect(inputProblem.pads.map((pad) => pad.padId)).toEqual([
    polygonPad.pcb_plated_hole_id,
    rectPad.pcb_plated_hole_id,
  ])
  expect(
    inputProblem.pads.some(
      (pad) => pad.padId === polygonPad.pcb_plated_hole_id,
    ),
  ).toBe(true)
  const output = new CopperPourPipelineSolver(inputProblem).getOutput()
  expect(output.brep_shapes).toHaveLength(1)
  const clearanceRings = output.brep_shapes[0]!.inner_rings
  expect(clearanceRings).toHaveLength(2)
  for (const pad of platedHoles) {
    const ring = clearanceRings.find((ring) =>
      ring.vertices.every((point) => Math.abs(point.x - pad.x) < 2),
    )!
    expect(Math.min(...ring.vertices.map((point) => point.x))).toBeCloseTo(
      pad.x - 1 - clearance,
    )
    expect(Math.max(...ring.vertices.map((point) => point.x))).toBeCloseTo(
      pad.x + 1 + clearance,
    )
    expect(Math.min(...ring.vertices.map((point) => point.y))).toBeCloseTo(
      -1 - clearance,
    )
    expect(Math.max(...ring.vertices.map((point) => point.y))).toBeCloseTo(
      1 + clearance,
    )
  }

  const svg = runSolverAndRenderToSvg(circuitJson, {
    layer: "bottom",
    net_name: "GND",
    pad_margin: clearance,
    trace_margin: clearance,
    board_edge_margin: 0.5,
  })
  await expect(svg).toMatchSvgSnapshot(import.meta.path)

  if (polygonPad.shape !== "hole_with_polygon_pad")
    throw new Error("Expected polygon pad")
  polygonPad.x = -3
  polygonPad.y = 1
  polygonPad.ccw_rotation = 90
  polygonPad.hole_offset_x = 0.25
  polygonPad.pad_outline = [
    { x: -1.5, y: -0.5 },
    { x: 1.5, y: -0.5 },
    { x: 1.5, y: 0.5 },
    { x: -1.5, y: 0.5 },
  ]
  for (const layer of ["top", "bottom"] as const) {
    const rotatedInput = convertCircuitJsonToInputProblem(circuitJson, {
      layer,
      source_net_name: "GND",
      pad_margin: clearance,
      trace_margin: clearance,
    })
    expect(
      rotatedInput.pads.find(
        (pad) => pad.padId === polygonPad.pcb_plated_hole_id,
      ),
    ).toMatchObject({
      shape: "polygon",
      isPlatedHole: true,
      points: [
        { x: expect.closeTo(-2.5), y: expect.closeTo(-0.5) },
        { x: expect.closeTo(-2.5), y: expect.closeTo(2.5) },
        { x: expect.closeTo(-3.5), y: expect.closeTo(2.5) },
        { x: expect.closeTo(-3.5), y: expect.closeTo(-0.5) },
      ],
    })
    const sameNetInput = convertCircuitJsonToInputProblem(circuitJson, {
      layer,
      source_net_name: "VCC",
      pad_margin: clearance,
      trace_margin: clearance,
    })
    const sameNetOutput = new CopperPourPipelineSolver(sameNetInput).getOutput()
    expect(sameNetOutput.brep_shapes).toHaveLength(1)
    expect(sameNetOutput.brep_shapes[0]!.inner_rings).toHaveLength(0)
  }
})
