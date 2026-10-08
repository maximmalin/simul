import { expect, test } from "bun:test"
import { Circuit } from "@tscircuit/core"
import type { AnyCircuitElement, BRepShape, SourceNet } from "circuit-json"
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
} from "lib/index"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

type Point = { x: number; y: number }

const leftPourOutline = [
  { x: -5, y: -3 },
  { x: 0, y: -3 },
  { x: 0, y: 3 },
  { x: -5, y: 3 },
]

const rightPourOutline = [
  { x: 0, y: -3 },
  { x: 5, y: -3 },
  { x: 5, y: 3 },
  { x: 0, y: 3 },
]

const pcbBoardOutline = [
  { x: -5, y: -3 },
  { x: 5, y: -3 },
  { x: 5, y: 3 },
  { x: -5, y: 3 },
]

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

test("adjacent same-net pours have no clearance at their shared edge", async () => {
  const circuit = new Circuit()
  circuit.add(
    <board width="10mm" height="6mm">
      <net name="GND" isGroundNet />
    </board>,
  )
  await circuit.renderUntilSettled()

  const circuitJson = circuit.getCircuitJson() as AnyCircuitElement[]
  const sourceNet = circuitJson.find(
    (element): element is SourceNet =>
      element.type === "source_net" && element.name === "GND",
  )
  if (!sourceNet) throw new Error("GND source net not found")

  const pourOptions = [leftPourOutline, rightPourOutline].map((outline) => ({
    layer: "top" as const,
    source_net_id: sourceNet.source_net_id,
    pad_margin: 0.2,
    trace_margin: 0.2,
    pour_margin: 0.2,
    board_edge_margin: 0.5,
    board_edge_outline: pcbBoardOutline,
    outline,
  }))
  const inputProblem = convertCircuitJsonToInputProblem(
    circuitJson,
    pourOptions,
  )
  const { brep_shapes: brepShapes } = new CopperPourPipelineSolver(
    inputProblem,
  ).getOutput()

  expect(
    brepShapes.some((brepShape) =>
      isPointInBrepShape({ x: -0.1, y: 0 }, brepShape),
    ),
  ).toBe(true)
  expect(
    brepShapes.some((brepShape) =>
      isPointInBrepShape({ x: 0.1, y: 0 }, brepShape),
    ),
  ).toBe(true)
  expect(
    brepShapes.some((brepShape) =>
      isPointInBrepShape({ x: -4.75, y: 0 }, brepShape),
    ),
  ).toBe(false)
  expect(
    brepShapes.some((brepShape) =>
      isPointInBrepShape({ x: 4.75, y: 0 }, brepShape),
    ),
  ).toBe(false)

  const svg = runSolverAndRenderToSvg(circuitJson, [
    {
      layer: "top",
      net_name: "GND",
      pad_margin: 0.2,
      trace_margin: 0.2,
      pour_margin: 0.2,
      board_edge_margin: 0.5,
      board_edge_outline: pcbBoardOutline,
      outline: leftPourOutline,
    },
    {
      layer: "top",
      net_name: "GND",
      pad_margin: 0.2,
      trace_margin: 0.2,
      pour_margin: 0.2,
      board_edge_margin: 0.5,
      board_edge_outline: pcbBoardOutline,
      outline: rightPourOutline,
    },
  ])

  await expect(svg).toMatchSvgSnapshot(
    import.meta.path,
    "same-net-adjacent-pours",
  )
})
