import { expect, test } from "bun:test"
import { Circuit } from "@tscircuit/core"
import type { PcbHoleOval } from "circuit-json"
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
} from "lib/index"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

test("non-plated oval hole receives copper-pour clearance", async () => {
  const clearance = 0.3
  const roundHoleX = -3
  const ovalHoleX = 3
  const ovalHoleWidth = 4
  const ovalHoleHeight = 2
  const circuit = new Circuit()
  circuit.add(
    <board width={16} height={10} routingDisabled>
      <net name="GND" isGroundNet />
      <hole name="ROUND" diameter={2} pcbX={roundHoleX} />
      <pcbnotetext
        text={`GND POUR: ${clearance} mm clearance`}
        pcbY={3.5}
        fontSize={0.6}
      />
      <pcbnotetext text="ROUND" pcbX={roundHoleX} pcbY={2} fontSize={0.5} />
      <pcbnotetext
        text="NON-PLATED OVAL"
        pcbX={ovalHoleX}
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

  const ovalHole: PcbHoleOval = {
    type: "pcb_hole",
    pcb_hole_id: "pcb_hole_oval_repro",
    hole_shape: "oval",
    hole_width: ovalHoleWidth,
    hole_height: ovalHoleHeight,
    x: ovalHoleX,
    y: 0,
  }
  const circuitJson = [...circuit.getCircuitJson(), ovalHole]
  const holes = circuitJson.filter((element) => element.type === "pcb_hole")
  expect(holes).toHaveLength(2)
  const roundHole = holes.find((hole) => hole.hole_shape === "circle")!
  expect(holes.find((hole) => hole.hole_shape === "oval")).toMatchObject({
    hole_width: ovalHoleWidth,
    hole_height: ovalHoleHeight,
    x: ovalHoleX,
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
    expect(input.pads).toHaveLength(2)
    expect(
      input.pads.find((pad) => pad.padId === roundHole.pcb_hole_id),
    ).toBeDefined()
    expect(
      input.pads.find((pad) => pad.padId === ovalHole.pcb_hole_id),
    ).toMatchObject({
      shape: "oval",
      layer,
      connectivityKey: `hole:${ovalHole.pcb_hole_id}`,
      x: ovalHole.x,
      y: ovalHole.y,
      width: ovalHole.hole_width,
      height: ovalHole.hole_height,
      ccwRotation: 0,
    })
    const output = new CopperPourPipelineSolver(input).getOutput()
    expect(output.brep_shapes).toHaveLength(1)
    expect(output.brep_shapes[0]!.inner_rings).toHaveLength(2)
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

for (const [width, height] of [
  [4, 2],
  [2, 4],
  [2, 2],
] as const) {
  for (const clearance of [0, 0.3]) {
    test(`oval hole ${width}x${height} uses ${clearance} mm cutout clearance`, () => {
      const hole: PcbHoleOval = {
        type: "pcb_hole",
        pcb_hole_id: "pcb_hole_oval",
        hole_shape: "oval",
        hole_width: width,
        hole_height: height,
        x: 1,
        y: -1,
      }
      for (const layer of ["top", "inner1", "bottom"] as const) {
        const input = convertCircuitJsonToInputProblem(
          [
            {
              type: "pcb_board",
              pcb_board_id: "pcb_board_0",
              center: { x: 0, y: 0 },
              width: 12,
              height: 12,
              thickness: 1.6,
              num_layers: 4,
              material: "fr4",
            },
            hole,
          ],
          {
            layer,
            subcircuit_connectivity_map_key: "net_gnd",
            pad_margin: 0.5,
            trace_margin: 0.5,
            cutout_margin: clearance,
          },
        )
        const output = new CopperPourPipelineSolver(input).getOutput()
        expect(output.brep_shapes).toHaveLength(1)
        const rings = output.brep_shapes[0]!.inner_rings
        expect(rings).toHaveLength(1)
        const xs = rings[0]!.vertices.map(({ x }) => x)
        const ys = rings[0]!.vertices.map(({ y }) => y)
        expect(Math.min(...xs)).toBeCloseTo(
          hole.x - hole.hole_width / 2 - clearance,
          2,
        )
        expect(Math.max(...xs)).toBeCloseTo(
          hole.x + hole.hole_width / 2 + clearance,
          2,
        )
        expect(Math.min(...ys)).toBeCloseTo(
          hole.y - hole.hole_height / 2 - clearance,
          2,
        )
        expect(Math.max(...ys)).toBeCloseTo(
          hole.y + hole.hole_height / 2 + clearance,
          2,
        )
      }
    })
  }
}
