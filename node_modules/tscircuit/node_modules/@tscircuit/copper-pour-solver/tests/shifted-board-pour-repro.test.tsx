import { expect, test } from "bun:test"
import { Circuit } from "@tscircuit/core"
import type { BRepShape, PcbBoard } from "circuit-json"
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
} from "lib/index"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

const boardCenter = { x: 8, y: 0 }
const boardWidth = 10
const boardHeight = 6

const getBrepBounds = (brepShape: BRepShape) => {
  const vertices = brepShape.outer_ring.vertices
  return {
    minX: Math.min(...vertices.map((vertex) => vertex.x)),
    minY: Math.min(...vertices.map((vertex) => vertex.y)),
    maxX: Math.max(...vertices.map((vertex) => vertex.x)),
    maxY: Math.max(...vertices.map((vertex) => vertex.y)),
  }
}

test("converter aligns a shifted board's pour without an outline", async () => {
  const circuit = new Circuit()
  circuit.add(
    <board
      width={boardWidth}
      height={boardHeight}
      pcbX={boardCenter.x}
      pcbY={boardCenter.y}
      routingDisabled
    >
      <net name="GND" isGroundNet />
      <pcbnotetext text="SHIFTED BOARD" pcbY={2} fontSize={0.45} />
      <pcbnotetext
        text="DIRECT CONVERTER: POUR FOLLOWS BOARD"
        pcbY={-2}
        fontSize={0.35}
      />
    </board>,
  )
  await circuit.renderUntilSettled()

  const circuitJson = circuit.getCircuitJson()
  const pcbBoard = circuitJson.find(
    (element): element is PcbBoard => element.type === "pcb_board",
  )
  if (!pcbBoard) throw new Error("PCB board not found")
  expect(pcbBoard.center).toEqual(boardCenter)

  const inputProblem = convertCircuitJsonToInputProblem(circuitJson, {
    layer: "top",
    source_net_name: "GND",
    pad_margin: 0.2,
    trace_margin: 0.2,
  })
  const expectedBoardBounds = {
    minX: boardCenter.x - boardWidth / 2,
    minY: boardCenter.y - boardHeight / 2,
    maxX: boardCenter.x + boardWidth / 2,
    maxY: boardCenter.y + boardHeight / 2,
  }
  expect(inputProblem.regionsForPour[0]?.bounds).toEqual(expectedBoardBounds)

  const output = new CopperPourPipelineSolver(inputProblem).getOutput()
  expect(output.brep_shapes).toHaveLength(1)
  expect(getBrepBounds(output.brep_shapes[0]!)).toEqual(expectedBoardBounds)

  expect(
    runSolverAndRenderToSvg(circuitJson, {
      layer: "top",
      net_name: "GND",
      pad_margin: 0.2,
      trace_margin: 0.2,
    }),
  ).toMatchSvgSnapshot(import.meta.path)
})
