import { expect, test } from "bun:test"
import type { BRepShape } from "circuit-json"
import { CopperPourPipelineSolver } from "lib/index"
import {
  composeCrossSections,
  crossSectionFromPolygon,
  crossSectionToCopperPourIslands,
  removeTinyIslands,
} from "lib/solvers/copper-pour/manifold-geometry-adapter"
import type { InputPad, InputProblem } from "lib/types"

const baseProblem = (pads: InputPad[]): InputProblem => ({
  regionsForPour: [
    {
      shape: "rect",
      layer: "top",
      bounds: { minX: 0, minY: 0, maxX: 10, maxY: 10 },
      connectivityKey: "net:GND",
      padMargin: 0.2,
      traceMargin: 0.2,
    },
  ],
  pads,
})

const solve = (pads: InputPad[]) =>
  new CopperPourPipelineSolver(baseProblem(pads)).getOutput().brep_shapes

const ringArea = (ring: { vertices: { x: number; y: number }[] }) => {
  let area = 0
  for (let i = 0; i < ring.vertices.length; i++) {
    const current = ring.vertices[i]!
    const next = ring.vertices[(i + 1) % ring.vertices.length]!
    area += current.x * next.y - next.x * current.y
  }
  return area / 2
}

const brepArea = (shape: BRepShape) =>
  Math.abs(ringArea(shape.outer_ring)) -
  shape.inner_rings.reduce((sum, ring) => sum + Math.abs(ringArea(ring)), 0)

const totalArea = (shapes: BRepShape[]) =>
  shapes.reduce((sum, shape) => sum + brepArea(shape), 0)

const assertValidRings = (shapes: BRepShape[]) => {
  for (const shape of shapes) {
    expect(shape.outer_ring.vertices.length).toBeGreaterThanOrEqual(3)
    for (const point of shape.outer_ring.vertices) {
      expect(Number.isFinite(point.x)).toBe(true)
      expect(Number.isFinite(point.y)).toBe(true)
    }
    for (const ring of shape.inner_rings) {
      expect(ring.vertices.length).toBeGreaterThanOrEqual(3)
      for (const point of ring.vertices) {
        expect(Number.isFinite(point.x)).toBe(true)
        expect(Number.isFinite(point.y)).toBe(true)
      }
    }
  }
}

test("empty blocker list still returns full pour", () => {
  const shapes = solve([])

  expect(shapes).toHaveLength(1)
  expect(shapes[0]!.inner_rings).toHaveLength(0)
  expect(totalArea(shapes)).toBe(100)
  assertValidRings(shapes)
})

test("trace clearance subtracts from rectangular pour", () => {
  const shapes = solve([
    {
      padId: "trace-1",
      shape: "trace",
      layer: "top",
      connectivityKey: "net:OTHER",
      width: 0.4,
      segments: [
        { x: 2, y: 5 },
        { x: 8, y: 5 },
      ],
    },
  ])

  expect(shapes).toHaveLength(1)
  expect(shapes[0]!.inner_rings.length).toBe(1)
  expect(totalArea(shapes)).toBeLessThan(100)
  assertValidRings(shapes)
})

test("overlapping near-touching trace clearances compose before subtraction", () => {
  const shapes = solve([
    {
      padId: "trace-1",
      shape: "trace",
      layer: "top",
      connectivityKey: "net:OTHER",
      width: 0.4,
      segments: [
        { x: 2, y: 4.7 },
        { x: 8, y: 4.7 },
      ],
    },
    {
      padId: "trace-2",
      shape: "trace",
      layer: "top",
      connectivityKey: "net:OTHER",
      width: 0.4,
      segments: [
        { x: 2, y: 5.25 },
        { x: 8, y: 5.25 },
      ],
    },
  ])

  expect(shapes).toHaveLength(1)
  expect(shapes[0]!.inner_rings.length).toBe(1)
  expect(totalArea(shapes)).toBeGreaterThan(90)
  assertValidRings(shapes)
})

test("pads and vias inside pour become holes", () => {
  const shapes = solve([
    {
      padId: "pad-1",
      shape: "rect",
      layer: "top",
      connectivityKey: "net:OTHER",
      bounds: { minX: 2, minY: 2, maxX: 3, maxY: 3 },
    },
    {
      padId: "via-1",
      shape: "circle",
      layer: "top",
      connectivityKey: "net:OTHER",
      x: 7,
      y: 7,
      radius: 0.35,
    },
  ])

  expect(shapes).toHaveLength(1)
  expect(shapes[0]!.inner_rings.length).toBe(2)
  assertValidRings(shapes)
})

test("same-net plated holes use the configured thermal relief spokes", () => {
  const problem = baseProblem([
    {
      padId: "plated-hole-1",
      shape: "circle",
      layer: "top",
      connectivityKey: "net:GND",
      isPlatedHole: true,
      x: 5,
      y: 5,
      radius: 0.6,
    },
  ])
  problem.regionsForPour[0]!.use_thermal_reliefs = true
  problem.regionsForPour[0]!.thermal_relief_spoke_width = 0.25
  problem.regionsForPour[0]!.thermal_relief_spoke_count = 3

  const shapes = new CopperPourPipelineSolver(problem).getOutput().brep_shapes

  expect(shapes).toHaveLength(1)
  expect(shapes[0]!.inner_rings).toHaveLength(3)
  expect(totalArea(shapes)).toBeLessThan(100)
  assertValidRings(shapes)
})

test("thermal relief options validate spoke dimensions", () => {
  const problem = baseProblem([
    {
      padId: "plated-hole-1",
      shape: "circle",
      layer: "top",
      connectivityKey: "net:GND",
      isPlatedHole: true,
      x: 5,
      y: 5,
      radius: 0.6,
    },
  ])
  problem.regionsForPour[0]!.use_thermal_reliefs = true
  problem.regionsForPour[0]!.thermal_relief_spoke_width = 0
  problem.regionsForPour[0]!.thermal_relief_spoke_count = 2.5

  expect(() => new CopperPourPipelineSolver(problem).getOutput()).toThrow(
    /thermal_relief_spoke_width/,
  )

  problem.regionsForPour[0]!.thermal_relief_spoke_width = 0.25
  expect(() => new CopperPourPipelineSolver(problem).getOutput()).toThrow(
    /thermal_relief_spoke_count/,
  )
})

test("blocker covering pour returns empty result", () => {
  const shapes = solve([
    {
      padId: "pad-1",
      shape: "rect",
      layer: "top",
      connectivityKey: "net:OTHER",
      bounds: { minX: -1, minY: -1, maxX: 11, maxY: 11 },
    },
  ])

  expect(shapes).toHaveLength(0)
})

test("clearance can split pour into multiple disconnected islands", () => {
  const shapes = solve([
    {
      padId: "trace-1",
      shape: "trace",
      layer: "top",
      connectivityKey: "net:OTHER",
      width: 0.6,
      segments: [
        { x: 5, y: -1 },
        { x: 5, y: 11 },
      ],
    },
  ])

  expect(shapes).toHaveLength(2)
  assertValidRings(shapes)
})

test("tiny copper islands are removed below threshold", () => {
  const normalIsland = crossSectionFromPolygon([
    { x: 0, y: 0 },
    { x: 1, y: 0 },
    { x: 1, y: 1 },
    { x: 0, y: 1 },
  ])
  const tinyIsland = crossSectionFromPolygon([
    { x: 2, y: 0 },
    { x: 2.00001, y: 0 },
    { x: 2.00001, y: 0.00001 },
    { x: 2, y: 0.00001 },
  ])

  const cleaned = removeTinyIslands(
    composeCrossSections([normalIsland, tinyIsland]),
    1e-8,
  )

  expect(crossSectionToCopperPourIslands(cleaned)).toHaveLength(1)
})
