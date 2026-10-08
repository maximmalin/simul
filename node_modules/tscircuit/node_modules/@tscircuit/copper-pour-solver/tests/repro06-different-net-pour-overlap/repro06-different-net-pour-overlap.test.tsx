import { expect, test } from "bun:test"
import { Circuit } from "@tscircuit/core"
import type { AnyCircuitElement, BRepShape, SourceNet } from "circuit-json"
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
} from "lib/index"
import { runSolverAndRenderToSvg } from "../utils/run-solver-and-render-to-svg"

type Point = { x: number; y: number }

const aisenPourOutline = [
  { x: -2, y: -2 },
  { x: 2, y: -2 },
  { x: 2, y: 2 },
  { x: -2, y: 2 },
  { x: -2, y: -2 },
]

const DifferentNetPourOverlapRepro = () => (
  <board width="20mm" height="12mm">
    <net name="GND" isGroundNet />
    <net name="AISEN" />

    <copperpour
      name="GND_BOTTOM"
      layer="bottom"
      connectsTo="net.GND"
      clearance="0.2mm"
    />
    <copperpour
      name="AISEN_BOTTOM"
      layer="bottom"
      connectsTo="net.AISEN"
      clearance="0.2mm"
      outline={aisenPourOutline}
    />

    <pcbnotetext
      text="Different-net bottom pours are mutually cleared"
      pcbY={5.15}
      fontSize="0.45mm"
      color="#ffffff"
    />
    <pcbnoterect
      width="19mm"
      height="11mm"
      strokeWidth="0.12mm"
      color="#60a5fa"
      isStrokeDashed
    />
    <pcbnotetext
      text="GND: full-board pour"
      pcbX={-5.8}
      pcbY={4.25}
      fontSize="0.4mm"
      color="#60a5fa"
    />
    <pcbnoteline
      x1={-7.2}
      y1={4}
      x2={-8.4}
      y2={3.35}
      strokeWidth="0.12mm"
      color="#60a5fa"
    />
    <pcbnoterect
      width="4mm"
      height="4mm"
      strokeWidth="0.16mm"
      color="#facc15"
    />
    <pcbnotetext
      text="AISEN: inset pour"
      pcbX={5.1}
      pcbY={2.7}
      fontSize="0.4mm"
      color="#facc15"
    />
    <pcbnoteline
      x1={3.8}
      y1={2.45}
      x2={2}
      y2={1.5}
      strokeWidth="0.12mm"
      color="#facc15"
    />
    <pcbnoteline
      x1={-0.45}
      y1={0}
      x2={-0.1}
      y2={-0.35}
      strokeWidth="0.18mm"
      color="#34d399"
    />
    <pcbnoteline
      x1={-0.1}
      y1={-0.35}
      x2={0.55}
      y2={0.4}
      strokeWidth="0.18mm"
      color="#34d399"
    />
    <pcbnoteline
      x1={0}
      y1={-0.55}
      x2={0}
      y2={-2.45}
      strokeWidth="0.12mm"
      color="#34d399"
    />
    <pcbnotetext
      text="FIXED: center belongs to AISEN only"
      pcbY={-2.85}
      fontSize="0.45mm"
      color="#34d399"
    />
    <pcbnotetext
      text="0.2 mm clearance between pours"
      pcbY={-4.65}
      fontSize="0.4mm"
      color="#34d399"
    />
  </board>
)

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

const solvePours = (circuitJson: AnyCircuitElement[]) => {
  const sourceNets = ["GND", "AISEN"].map((netName) => {
    const sourceNet = circuitJson.find(
      (element): element is SourceNet =>
        element.type === "source_net" && element.name === netName,
    )

    if (!sourceNet) throw new Error(`Source net "${netName}" not found`)
    return sourceNet
  })

  const inputProblem = convertCircuitJsonToInputProblem(circuitJson, [
    {
      layer: "bottom",
      source_net_id: sourceNets[0]!.source_net_id,
      pad_margin: 0.2,
      trace_margin: 0.2,
      pour_margin: 0.2,
      board_edge_margin: 0.2,
    },
    {
      layer: "bottom",
      source_net_id: sourceNets[1]!.source_net_id,
      pad_margin: 0.2,
      trace_margin: 0.2,
      pour_margin: 0.2,
      board_edge_margin: 0.2,
      outline: aisenPourOutline,
    },
  ])

  return new CopperPourPipelineSolver(inputProblem).getOutput()
}

test("different-net copper pours on the same layer should not overlap", async () => {
  const circuit = new Circuit()
  circuit.add(<DifferentNetPourOverlapRepro />)
  await circuit.renderUntilSettled()

  const circuitJson = circuit
    .getCircuitJson()
    .filter(
      (element) => element.type !== "pcb_copper_pour",
    ) as AnyCircuitElement[]

  const { brep_shapes: brepShapes, brep_shapes_by_region } =
    solvePours(circuitJson)
  const poursContainingBoardCenter = brepShapes.filter((brepShape) =>
    isPointInBrepShape({ x: 0, y: 0 }, brepShape),
  )
  const poursContainingClearanceGap = brepShapes.filter((brepShape) =>
    isPointInBrepShape({ x: 1.9, y: 0 }, brepShape),
  )

  const svg = runSolverAndRenderToSvg(circuitJson, [
    {
      layer: "bottom",
      net_name: "GND",
      pad_margin: 0.2,
      trace_margin: 0.2,
      pour_margin: 0.2,
      board_edge_margin: 0.2,
    },
    {
      layer: "bottom",
      net_name: "AISEN",
      pad_margin: 0.2,
      trace_margin: 0.2,
      pour_margin: 0.2,
      board_edge_margin: 0.2,
      outline: aisenPourOutline,
    },
  ])

  await expect(svg).toMatchSvgSnapshot(
    import.meta.path,
    "repro06-different-net-pour-overlap",
  )
  expect(brepShapes).toHaveLength(2)
  expect(brep_shapes_by_region).toHaveLength(2)
  expect(brep_shapes_by_region[0]).toHaveLength(1)
  expect(brep_shapes_by_region[1]).toHaveLength(1)
  expect(poursContainingBoardCenter).toHaveLength(1)
  expect(
    isPointInBrepShape({ x: 0, y: 0 }, brep_shapes_by_region[1]![0]!),
  ).toBe(true)
  expect(poursContainingClearanceGap).toHaveLength(0)
})
