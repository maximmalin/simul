import { expect, test } from "bun:test"
import { Circuit } from "@tscircuit/core"
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
} from "lib/index"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

test("non-plated round and rectangular holes receive copper-pour clearance", async () => {
  const clearance = 0.3
  const roundHoleX = -3
  const rectangularHoleX = 3
  const rectangularHoleWidth = 4
  const rectangularHoleHeight = 2
  const circuit = new Circuit()
  circuit.add(
    <board width={16} height={10} routingDisabled>
      <net name="GND" isGroundNet />
      <hole name="ROUND" diameter={2} pcbX={roundHoleX} />
      <hole
        name="RECT"
        shape="rect"
        width={rectangularHoleWidth}
        height={rectangularHoleHeight}
        pcbX={rectangularHoleX}
      />
      <pcbnotetext
        text={`GND POUR: ${clearance} mm clearance`}
        pcbY={3.5}
        fontSize={0.6}
      />
      <pcbnotetext text="ROUND" pcbX={roundHoleX} pcbY={2} fontSize={0.5} />
      <pcbnotetext
        text="NON-PLATED RECT"
        pcbX={rectangularHoleX}
        pcbY={2}
        fontSize={0.5}
      />
      <pcbnotetext
        text="Expected: clearance around both openings"
        pcbY={-3}
        fontSize={0.5}
      />
    </board>,
  )
  await circuit.renderUntilSettled()
  const circuitJson = circuit.getCircuitJson()
  const holes = circuitJson.filter((element) => element.type === "pcb_hole")
  expect(holes).toHaveLength(2)
  const roundHole = holes.find((hole) => hole.hole_shape === "circle")!
  const rectangularHole = holes.find((hole) => hole.hole_shape === "rect")!
  expect(rectangularHole).toMatchObject({
    hole_width: rectangularHoleWidth,
    hole_height: rectangularHoleHeight,
    x: rectangularHoleX,
    y: 0,
  })

  for (const layer of ["top", "bottom"] as const) {
    const input = convertCircuitJsonToInputProblem(circuitJson, {
      layer,
      source_net_name: "GND",
      pad_margin: clearance,
      trace_margin: clearance,
      board_edge_margin: 0.5,
      cutout_margin: clearance,
    })
    expect(input.pads.map((pad) => pad.padId)).toEqual([
      roundHole.pcb_hole_id,
      rectangularHole.pcb_hole_id,
    ])
    expect(
      input.pads.find((pad) => pad.padId === rectangularHole.pcb_hole_id),
    ).toMatchObject({
      shape: "rect",
      layer,
      bounds: {
        minX: rectangularHoleX - rectangularHoleWidth / 2,
        minY: -rectangularHoleHeight / 2,
        maxX: rectangularHoleX + rectangularHoleWidth / 2,
        maxY: rectangularHoleHeight / 2,
      },
    })
    const output = new CopperPourPipelineSolver(input).getOutput()
    expect(output.brep_shapes).toHaveLength(1)
    const clearanceRings = output.brep_shapes[0]!.inner_rings
    expect(clearanceRings).toHaveLength(2)
    const rectangularClearance = clearanceRings.find((ring) =>
      ring.vertices.every((point) => point.x > 0),
    )!
    expect(
      Math.min(...rectangularClearance.vertices.map((point) => point.x)),
    ).toBeCloseTo(rectangularHoleX - rectangularHoleWidth / 2 - clearance)
    expect(
      Math.max(...rectangularClearance.vertices.map((point) => point.x)),
    ).toBeCloseTo(rectangularHoleX + rectangularHoleWidth / 2 + clearance)
    expect(
      Math.min(...rectangularClearance.vertices.map((point) => point.y)),
    ).toBeCloseTo(-rectangularHoleHeight / 2 - clearance)
    expect(
      Math.max(...rectangularClearance.vertices.map((point) => point.y)),
    ).toBeCloseTo(rectangularHoleHeight / 2 + clearance)
  }

  expect(
    runSolverAndRenderToSvg(circuitJson, {
      layer: "top",
      net_name: "GND",
      pad_margin: clearance,
      trace_margin: clearance,
      board_edge_margin: 0.5,
      cutout_margin: clearance,
    }),
  ).toMatchSvgSnapshot(import.meta.path)
})
