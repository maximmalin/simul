import { expect, test } from "bun:test"
import { CopperPourPipelineSolver } from "lib/index"
import type { InputProblem } from "lib/types"

const makeProblem = (
  padMargin: number,
  connectivityKey = "net:SIGNAL",
): InputProblem => ({
  regionsForPour: [
    {
      shape: "rect",
      layer: "top",
      bounds: { minX: -8, minY: -5, maxX: 8, maxY: 5 },
      connectivityKey: "net:GND",
      padMargin,
      traceMargin: 0.1,
      cutout_margin: 0.1,
    },
  ],
  pads: [
    {
      shape: "polygon",
      padId: "polygon-pad",
      layer: "top",
      connectivityKey,
      isSmtPad: true,
      // Circuit world points in mm, +X right and +Y up.
      points: [
        { x: 2, y: -1 },
        { x: 4, y: -1 },
        { x: 4, y: 1 },
        { x: 2, y: 1 },
      ],
    },
  ],
})

test.each([0, 0.2, 0.5])("polygon pads respect padMargin=%s", (margin) => {
  const output = new CopperPourPipelineSolver(makeProblem(margin)).getOutput()
  expect(output.brep_shapes).toHaveLength(1)
  const holes = output.brep_shapes[0]!.inner_rings
  expect(holes).toHaveLength(1)
  const vertices = holes[0]!.vertices
  expect(Math.min(...vertices.map((point) => point.x))).toBeCloseTo(
    2 - margin,
    6,
  )
  expect(Math.max(...vertices.map((point) => point.x))).toBeCloseTo(
    4 + margin,
    6,
  )
  expect(Math.min(...vertices.map((point) => point.y))).toBeCloseTo(
    -1 - margin,
    6,
  )
  expect(Math.max(...vertices.map((point) => point.y))).toBeCloseTo(
    1 + margin,
    6,
  )
})

test.each([
  ["cutout:polygon", 0.1],
  ["hole:polygon", 0.1],
  ["keepout:polygon", 0],
] as const)("%s retains its own clearance rules", (connectivityKey, margin) => {
  const output = new CopperPourPipelineSolver(
    makeProblem(0.5, connectivityKey),
  ).getOutput()
  const holes = output.brep_shapes[0]!.inner_rings
  expect(holes).toHaveLength(1)
  const vertices = holes[0]!.vertices
  expect(Math.min(...vertices.map((point) => point.x))).toBeCloseTo(
    2 - margin,
    6,
  )
  expect(Math.max(...vertices.map((point) => point.x))).toBeCloseTo(
    4 + margin,
    6,
  )
})

test("same-net polygon pads still connect directly without thermal reliefs", () => {
  const output = new CopperPourPipelineSolver(
    makeProblem(0.5, "net:GND"),
  ).getOutput()
  expect(output.brep_shapes).toHaveLength(1)
  expect(output.brep_shapes[0]!.inner_rings).toHaveLength(0)
})
