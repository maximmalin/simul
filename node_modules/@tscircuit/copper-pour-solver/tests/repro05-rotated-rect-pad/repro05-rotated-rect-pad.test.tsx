import { expect, test } from "bun:test"
import { Circuit } from "@tscircuit/core"
import type { AnyCircuitElement } from "circuit-json"
import { runSolverAndRenderToSvg } from "../utils/run-solver-and-render-to-svg"

const SingleRectPadChip = ({
  name,
  pcbX,
  pcbRotation = 0,
}: {
  name: string
  pcbX: number
  pcbRotation?: number
}) => (
  <chip
    name={name}
    pcbX={pcbX}
    pcbRotation={pcbRotation}
    footprint={
      <footprint>
        <smtpad shape="rect" width="2mm" height="1mm" portHints={["pin1"]} />
      </footprint>
    }
  />
)

const RotatedRectPadRepro = () => (
  <board width="10mm" height="6mm">
    <net name="GND" isGroundNet />

    <SingleRectPadChip name="U_RECT" pcbX={-2.5} />
    <SingleRectPadChip name="U_ROTATED" pcbX={2.5} pcbRotation={45} />

    <copperpour
      name="GND_TOP"
      layer="top"
      connectsTo="net.GND"
      padMargin="0.4mm"
      traceMargin="0.2mm"
    />

    <pcbnotetext
      text="rect"
      pcbX={-2.5}
      pcbY={-1.4}
      fontSize="0.3mm"
      anchorAlignment="center"
      color="#ffffff"
    />
    <pcbnotetext
      text="rotated_rect"
      pcbX={2.5}
      pcbY={-1.4}
      fontSize="0.3mm"
      anchorAlignment="center"
      color="#ffffff"
    />
  </board>
)

test("rotated_rect pads are cleared by copper pours", async () => {
  const circuit = new Circuit()
  circuit.add(<RotatedRectPadRepro />)
  await circuit.renderUntilSettled()

  const circuitJson = circuit.getCircuitJson()
  const padShapes = circuitJson
    .filter((element) => element.type === "pcb_smtpad")
    .map((pad) => pad.shape)
    .sort()

  expect(padShapes).toEqual(["rect", "rotated_rect"])

  const svg = runSolverAndRenderToSvg(
    circuitJson.filter(
      (element) => element.type !== "pcb_copper_pour",
    ) as AnyCircuitElement[],
    {
      layer: "top",
      net_name: "GND",
      pad_margin: 0.4,
      trace_margin: 0.2,
    },
  )

  await expect(svg).toMatchSvgSnapshot(
    import.meta.path,
    "repro05-rotated-rect-pad",
  )
})
