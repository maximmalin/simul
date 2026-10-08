import { expect, test } from "bun:test"
import type { BRepShape } from "circuit-json"
import { CopperPourPipelineSolver } from "lib/index"
import type { InputProblem } from "lib/types"

type Point = { x: number; y: number }

const isPointInPolygon = (point: Point, vertices: Point[]) => {
  let isInside = false

  for (
    let vertexIndex = 0, previousVertexIndex = vertices.length - 1;
    vertexIndex < vertices.length;
    previousVertexIndex = vertexIndex++
  ) {
    const vertex = vertices[vertexIndex]!
    const previousVertex = vertices[previousVertexIndex]!

    if (
      vertex.y > point.y !== previousVertex.y > point.y &&
      point.x <
        ((previousVertex.x - vertex.x) * (point.y - vertex.y)) /
          (previousVertex.y - vertex.y) +
          vertex.x
    ) {
      isInside = !isInside
    }
  }

  return isInside
}

const isPointInBrepShape = (point: Point, brepShape: BRepShape) =>
  isPointInPolygon(point, brepShape.outer_ring.vertices) &&
  !brepShape.inner_rings.some((innerRing) =>
    isPointInPolygon(point, innerRing.vertices),
  )

const makeRegion = ({
  layer,
  connectivityKey,
  bounds,
  pourMargin = 0.2,
}: {
  layer: string
  connectivityKey: string
  bounds: { minX: number; minY: number; maxX: number; maxY: number }
  pourMargin?: number
}) => ({
  shape: "rect" as const,
  layer,
  bounds,
  connectivityKey,
  padMargin: 0.2,
  traceMargin: 0.2,
  pourMargin,
})

test("later different-net regions take priority with pour clearance", () => {
  const input: InputProblem = {
    pads: [],
    regionsForPour: [
      makeRegion({
        layer: "bottom",
        connectivityKey: "net:GND",
        bounds: { minX: -5, minY: -5, maxX: 5, maxY: 5 },
      }),
      makeRegion({
        layer: "bottom",
        connectivityKey: "net:AISEN",
        bounds: { minX: -2, minY: -2, maxX: 2, maxY: 2 },
        pourMargin: 0.4,
      }),
    ],
  }

  const output = new CopperPourPipelineSolver(input).getOutput()
  const [gndShapes, aisenShapes] = output.brep_shapes_by_region

  expect(gndShapes).toHaveLength(1)
  expect(aisenShapes).toHaveLength(1)
  expect(isPointInBrepShape({ x: 0, y: 0 }, gndShapes![0]!)).toBe(false)
  expect(isPointInBrepShape({ x: 0, y: 0 }, aisenShapes![0]!)).toBe(true)
  expect(
    output.brep_shapes.some((shape) =>
      isPointInBrepShape({ x: 2.3, y: 0 }, shape),
    ),
  ).toBe(false)
  expect(isPointInBrepShape({ x: 2.5, y: 0 }, gndShapes![0]!)).toBe(true)
})

test("same-net regions on the same layer do not block each other", () => {
  const sharedBounds = { minX: -2, minY: -2, maxX: 2, maxY: 2 }
  const input: InputProblem = {
    pads: [],
    regionsForPour: [
      makeRegion({
        layer: "bottom",
        connectivityKey: "net:GND",
        bounds: sharedBounds,
      }),
      makeRegion({
        layer: "bottom",
        connectivityKey: "net:GND",
        bounds: sharedBounds,
      }),
    ],
  }

  const output = new CopperPourPipelineSolver(input).getOutput()

  expect(
    output.brep_shapes_by_region.every((shapes) =>
      isPointInBrepShape({ x: 0, y: 0 }, shapes[0]!),
    ),
  ).toBe(true)
})

test("pour regions on different layers do not block each other", () => {
  const sharedBounds = { minX: -2, minY: -2, maxX: 2, maxY: 2 }
  const input: InputProblem = {
    pads: [],
    regionsForPour: [
      makeRegion({
        layer: "top",
        connectivityKey: "net:GND",
        bounds: sharedBounds,
      }),
      makeRegion({
        layer: "bottom",
        connectivityKey: "net:VCC",
        bounds: sharedBounds,
      }),
    ],
  }

  const output = new CopperPourPipelineSolver(input).getOutput()

  expect(output.brep_shapes_by_region[0]).toHaveLength(1)
  expect(output.brep_shapes_by_region[1]).toHaveLength(1)
  expect(
    output.brep_shapes_by_region.every((shapes) =>
      isPointInBrepShape({ x: 0, y: 0 }, shapes[0]!),
    ),
  ).toBe(true)
})
