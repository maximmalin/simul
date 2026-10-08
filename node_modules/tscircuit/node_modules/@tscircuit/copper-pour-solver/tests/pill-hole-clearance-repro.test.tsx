import { expect, test } from "bun:test"
import { Circuit } from "@tscircuit/core"
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
} from "lib/index"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

test.each([
  { width: 6, height: 2, rotation: 0, halfX: 3, halfY: 1 },
  { width: 2, height: 6, rotation: 0, halfX: 1, halfY: 3 },
  { width: 6, height: 2, rotation: 90, halfX: 1, halfY: 3 },
  {
    width: 6,
    height: 2,
    rotation: 45,
    halfX: 1 + Math.SQRT2,
    halfY: 1 + Math.SQRT2,
  },
  { width: 2, height: 2, rotation: 0, halfX: 1, halfY: 1 },
])(
  "pill $width x $height rotated $rotation has clearance on both layers",
  async ({ width, height, rotation, halfX, halfY }) => {
    const circuit = new Circuit()
    circuit.add(
      <board width={16} height={16} routingDisabled>
        <net name="GND" isGroundNet />
        <hole
          shape="pill"
          width={width}
          height={height}
          pcbRotation={rotation}
          pcbX={1}
          pcbY={-1}
        />
      </board>,
    )
    await circuit.renderUntilSettled()
    for (const layer of ["top", "bottom"] as const) {
      for (const clearance of [0, 0.3]) {
        const input = convertCircuitJsonToInputProblem(
          circuit.getCircuitJson(),
          {
            layer,
            source_net_name: "GND",
            pad_margin: 0.8,
            trace_margin: 0.8,
            cutout_margin: clearance,
          },
        )
        expect(input.pads).toHaveLength(1)
        expect(input.pads[0]).toMatchObject({
          shape: "pill",
          layer,
          x: 1,
          y: -1,
        })
        expect(input.pads[0]!.isPlatedHole).toBeUndefined()
        const output = new CopperPourPipelineSolver(input).getOutput()
        expect(output.brep_shapes).toHaveLength(1)
        expect(output.brep_shapes[0]!.inner_rings).toHaveLength(1)
        const vertices = output.brep_shapes[0]!.inner_rings[0]!.vertices
        expect(Math.min(...vertices.map((point) => point.x))).toBeCloseTo(
          1 - halfX - clearance,
          2,
        )
        expect(Math.max(...vertices.map((point) => point.x))).toBeCloseTo(
          1 + halfX + clearance,
          2,
        )
        expect(Math.min(...vertices.map((point) => point.y))).toBeCloseTo(
          -1 - halfY - clearance,
          2,
        )
        expect(Math.max(...vertices.map((point) => point.y))).toBeCloseTo(
          -1 + halfY + clearance,
          2,
        )
      }
    }
  },
)

test("non-plated round and pill holes receive copper-pour clearance", async () => {
  const clearance = 0.3
  const circuit = new Circuit()
  circuit.add(
    <board width={20} height={10} routingDisabled>
      <net name="GND" isGroundNet />
      <hole name="ROUND" diameter={2} pcbX={-5} />
      <hole name="SLOT" shape="pill" width={6} height={2} pcbX={3} />
      <pcbnotetext
        text="GND POUR: 0.3 mm clearance"
        pcbY={3.5}
        fontSize={0.6}
      />
      <pcbnotetext text="ROUND" pcbX={-5} pcbY={2} fontSize={0.5} />
      <pcbnotetext text="NON-PLATED SLOT" pcbX={3} pcbY={2} fontSize={0.5} />
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
  const slot = holes.find((hole) => hole.hole_shape === "pill")!
  expect(slot).toMatchObject({ hole_width: 6, hole_height: 2, x: 3, y: 0 })

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
      input.pads.find((pad) => pad.padId === slot.pcb_hole_id),
    ).toMatchObject({
      shape: "pill",
      x: 3,
      y: 0,
      width: 6,
      height: 2,
      radius: 1,
      ccwRotation: 0,
      layer,
    })
    const output = new CopperPourPipelineSolver(input).getOutput()
    expect(output.brep_shapes).toHaveLength(1)
    const rings = output.brep_shapes[0]!.inner_rings
    expect(rings).toHaveLength(2)
    const slotRing = rings.find((ring) =>
      ring.vertices.every((point) => point.x > -1),
    )!
    expect(Math.min(...slotRing.vertices.map((point) => point.x))).toBeCloseTo(
      -clearance,
    )
    expect(Math.max(...slotRing.vertices.map((point) => point.x))).toBeCloseTo(
      6 + clearance,
    )
    expect(Math.min(...slotRing.vertices.map((point) => point.y))).toBeCloseTo(
      -1 - clearance,
    )
    expect(Math.max(...slotRing.vertices.map((point) => point.y))).toBeCloseTo(
      1 + clearance,
    )
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
