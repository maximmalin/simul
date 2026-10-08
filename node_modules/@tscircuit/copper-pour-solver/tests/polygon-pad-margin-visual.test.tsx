import { expect, test } from "bun:test"
import { Circuit } from "@tscircuit/core"
import { runSolverAndRenderToSvg } from "./utils/run-solver-and-render-to-svg"

test("rectangle and polygon signal pads both have 0.5 mm copper clearance", async () => {
  const circuit = new Circuit()
  circuit.add(
    <board width="16mm" height="10mm" routingDisabled>
      <net name="GND" isGroundNet />
      <net name="RECT_SIGNAL" />
      <net name="POLYGON_SIGNAL" />
      <chip
        name="U1"
        connections={{ pin1: "net.RECT_SIGNAL", pin2: "net.POLYGON_SIGNAL" }}
        footprint={
          <footprint>
            <smtpad
              shape="rect"
              width="2mm"
              height="2mm"
              pcbX={-3}
              layer="top"
              portHints={["pin1"]}
            />
            <smtpad
              shape="polygon"
              layer="top"
              portHints={["pin2"]}
              points={[
                { x: 2, y: -1 },
                { x: 4, y: -1 },
                { x: 4, y: 1 },
                { x: 2, y: 1 },
              ]}
            />
          </footprint>
        }
      />
      <pcbnotetext
        text="GND POUR: padMargin = 0.5 mm"
        pcbY={3.6}
        fontSize="0.6mm"
      />
      <pcbnotetext text="RECT" pcbX={-3} pcbY={2.1} fontSize="0.55mm" />
      <pcbnotetext text="POLYGON" pcbX={3} pcbY={2.1} fontSize="0.55mm" />
      <pcbnotetext
        text="Expected: 0.5 mm gap around BOTH signal pads"
        pcbY={-3.2}
        fontSize="0.45mm"
      />
    </board>,
  )
  await circuit.renderUntilSettled()

  // Run the solver under test, not the version bundled with core.
  const svg = runSolverAndRenderToSvg(circuit.getCircuitJson(), {
    layer: "top",
    net_name: "GND",
    pad_margin: 0.5,
    trace_margin: 0.2,
    board_edge_margin: 0.2,
  })
  await expect(svg).toMatchSvgSnapshot(import.meta.path, "polygon-pad-margin")
})
