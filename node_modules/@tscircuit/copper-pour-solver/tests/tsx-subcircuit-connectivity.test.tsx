import { expect, test } from "bun:test"
import { Circuit } from "@tscircuit/core"
import { convertCircuitJsonToPcbSvg } from "circuit-to-svg"
import type {
  AnyCircuitElement,
  PcbCopperPourBRep,
  SourceNet,
} from "circuit-json"
import { CopperPourPipelineSolver } from "lib/index"
import { convertCircuitJsonToInputProblem } from "lib/circuit-json/convert-circuit-json-to-input-problem"

const SubcircuitChild = () => (
  <subcircuit
    name="SubcircuitChild"
    pcbX={2}
    pcbY={0}
    autorouter="sequential_trace"
  >
    <pinheader
      name="J2"
      pinCount={2}
      footprint="pinrow2"
      pcbX={0}
      pcbY={0}
      pinLabels={{ pin1: "B", pin2: "C" }}
      showSilkscreenPinLabels
      connections={{ C: "net.GND" }}
    />
  </subcircuit>
)

interface SubcircuitConnectivityReproProps {
  includeParentToChildTrace: boolean
}

const SubcircuitConnectivityRepro = ({
  includeParentToChildTrace,
}: SubcircuitConnectivityReproProps) => (
  <board
    name="SubcircuitParent"
    width="10mm"
    height="7mm"
    autorouter="sequential_trace"
  >
    <pinheader
      name="J1"
      pinCount={1}
      footprint="pinrow1"
      pcbX={-3}
      pcbY={0}
      pinLabels={{ pin1: "A" }}
      showSilkscreenPinLabels
      connections={{ A: "net.GND" }}
    />

    <SubcircuitChild />

    {includeParentToChildTrace && (
      <trace from="J1.A" to=".SubcircuitChild > net.GND" />
    )}

    <copperpour
      name="top_gnd_pour"
      layer="top"
      connectsTo="net.GND"
      padMargin="0.25mm"
      traceMargin="0.2mm"
    />

    <pcbnotetext
      text={
        includeParentToChildTrace
          ? "Expected: copper pour connects to A and C, but clears around B."
          : "Expected: copper pour connects to A, but clears around B and C."
      }
      pcbX={0}
      pcbY={4.35}
      fontSize="0.22mm"
      anchorAlignment="center"
      color="#ffffff"
    />
    <pcbnotetext
      text={
        includeParentToChildTrace
          ? "A is parent net.GND; C is child net.GND reached through the parent-to-child net."
          : "A is parent net.GND; C is child net.GND with no parent-to-child net."
      }
      pcbX={0}
      pcbY={4.05}
      fontSize="0.22mm"
      anchorAlignment="center"
      color="#ffffff"
    />
    <pcbnotetext
      text="B is a separate child pin, so the pour should clear around it."
      pcbX={0}
      pcbY={3.75}
      fontSize="0.22mm"
      anchorAlignment="center"
      color="#ffffff"
    />
  </board>
)

const renderCircuitJson = async (
  props: SubcircuitConnectivityReproProps,
): Promise<AnyCircuitElement[]> => {
  const circuit = new Circuit()
  const originalLog = console.log
  const originalWarn = console.warn
  console.log = () => {}
  console.warn = () => {}
  try {
    circuit.add(<SubcircuitConnectivityRepro {...props} />)
    await circuit.renderUntilSettled()
    return circuit.getCircuitJson()
  } finally {
    console.log = originalLog
    console.warn = originalWarn
  }
}

const getPadCenterX = (pad: any) => {
  if (typeof pad.x === "number") return pad.x
  return (pad.bounds.minX + pad.bounds.maxX) / 2
}

const getPcbPadConnectivityKeys = (inputProblem: any) => {
  const [aPad, bPad, cPad] = inputProblem.pads
    .filter((pad: any) => pad.shape === "circle" || pad.shape === "rect")
    .sort((a: any, b: any) => getPadCenterX(a) - getPadCenterX(b))

  return {
    a: aPad?.connectivityKey,
    b: bPad?.connectivityKey,
    c: cPad?.connectivityKey,
  }
}

const runSubcircuitConnectivityCase = async ({
  includeParentToChildTrace,
  snapshotName,
  expectedSourceNetConnectivityKeyCount,
  expectChildGndConnectedToPour,
}: {
  includeParentToChildTrace: boolean
  snapshotName: string
  expectedSourceNetConnectivityKeyCount: number
  expectChildGndConnectedToPour: boolean
}) => {
  const circuitJson = await renderCircuitJson({ includeParentToChildTrace })
  const gndSourceNets = circuitJson.filter(
    (element): element is SourceNet =>
      element.type === "source_net" && element.name === "GND",
  )

  expect(gndSourceNets).toHaveLength(2)
  expect(
    new Set(gndSourceNets.map((net) => net.subcircuit_connectivity_map_key))
      .size,
  ).toBe(expectedSourceNetConnectivityKeyCount)
  expect(new Set(gndSourceNets.map((net) => net.subcircuit_id)).size).toBe(2)

  const parentGndSourceNet = gndSourceNets.find((net) =>
    net.subcircuit_connectivity_map_key?.includes("SubcircuitParent"),
  )
  expect(parentGndSourceNet).toBeDefined()

  const inputProblem = convertCircuitJsonToInputProblem(circuitJson, {
    layer: "top",
    source_net_id: parentGndSourceNet!.source_net_id,
    pad_margin: 0.25,
    trace_margin: 0.2,
  })

  const pourConnectivityKey = inputProblem.regionsForPour[0]?.connectivityKey
  const padConnectivityKeys = getPcbPadConnectivityKeys(inputProblem)

  expect(padConnectivityKeys.a).toBe(pourConnectivityKey)
  expect(padConnectivityKeys.b).not.toBe(pourConnectivityKey)
  if (expectChildGndConnectedToPour) {
    expect(padConnectivityKeys.c).toBe(pourConnectivityKey)
  } else {
    expect(padConnectivityKeys.c).not.toBe(pourConnectivityKey)
  }

  const output = new CopperPourPipelineSolver(inputProblem).getOutput()
  const copperPours: PcbCopperPourBRep[] = output.brep_shapes.map(
    (brep_shape, i) => ({
      type: "pcb_copper_pour",
      shape: "brep",
      pcb_copper_pour_id: `pcb_copper_pour_tsx_subcircuit_${i}`,
      layer: "top",
      source_net_id: parentGndSourceNet!.source_net_id,
      brep_shape,
      covered_with_solder_mask: true,
    }),
  )
  const svg = convertCircuitJsonToPcbSvg([
    ...circuitJson.filter((element) => element.type !== "pcb_copper_pour"),
    ...copperPours,
  ] as any)

  await expect(svg).toMatchSvgSnapshot(import.meta.path, snapshotName)
}

test("tsx subcircuit connectivity 01 connects child net through parent-to-child trace", async () => {
  await runSubcircuitConnectivityCase({
    includeParentToChildTrace: true,
    snapshotName: "tsx-subcircuit-connectivity01",
    expectedSourceNetConnectivityKeyCount: 1,
    expectChildGndConnectedToPour: true,
  })
})

test("tsx subcircuit connectivity 02 keeps child net separate without parent-to-child trace", async () => {
  await runSubcircuitConnectivityCase({
    includeParentToChildTrace: false,
    snapshotName: "tsx-subcircuit-connectivity02",
    expectedSourceNetConnectivityKeyCount: 2,
    expectChildGndConnectedToPour: false,
  })
})
