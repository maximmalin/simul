import { expect, test } from "bun:test"
import type { AnyCircuitElement, PcbCopperPourBRep } from "circuit-json"
import { convertCircuitJsonToPcbSvg } from "circuit-to-svg"
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
} from "lib/index"
import type { InputProblem } from "lib/types"

const ch340cPadPositions = [
  { pin: 1, x: -4.445, y: -2.872486 },
  { pin: 2, x: -3.175, y: -2.872486 },
  { pin: 3, x: -1.905, y: -2.872486 },
  { pin: 4, x: -0.635, y: -2.872486 },
  { pin: 5, x: 0.635, y: -2.872486 },
  { pin: 6, x: 1.905, y: -2.872486 },
  { pin: 7, x: 3.175, y: -2.872486 },
  { pin: 8, x: 4.445, y: -2.872486 },
  { pin: 16, x: -4.445, y: 2.872486 },
  { pin: 15, x: -3.175, y: 2.872486 },
  { pin: 14, x: -1.905, y: 2.872486 },
  { pin: 13, x: -0.635, y: 2.872486 },
  { pin: 12, x: 0.635, y: 2.872486 },
  { pin: 11, x: 1.905, y: 2.872486 },
  { pin: 10, x: 3.175, y: 2.872486 },
  { pin: 9, x: 4.445, y: 2.872486 },
]

const circuitJson: AnyCircuitElement[] = [
  {
    type: "pcb_board",
    pcb_board_id: "pcb_board_0",
    center: { x: 0, y: 0 },
    width: 14,
    height: 10,
    thickness: 1.4,
    num_layers: 2,
    material: "fr4",
  },
  ...ch340cPadPositions.map(({ pin, x, y }) => ({
    type: "pcb_smtpad" as const,
    pcb_smtpad_id: `pcb_smtpad_ch340c_${pin}`,
    pcb_component_id: "pcb_component_ch340c",
    layer: "top" as const,
    shape: "pill" as const,
    x,
    y,
    width: 0.5599938,
    height: 1.7450054,
    radius: 0.2799969,
    port_hints: [`pin${pin}`],
    is_covered_with_solder_mask: false,
  })),
  {
    type: "pcb_smtpad",
    pcb_smtpad_id: "pcb_smtpad_rotated_pill",
    pcb_component_id: "pcb_component_rotated_pill",
    layer: "top",
    shape: "rotated_pill",
    x: 0,
    y: 0,
    width: 0.5599938,
    height: 1.7450054,
    radius: 0.2799969,
    ccw_rotation: 90,
    port_hints: ["rotated_pill"],
    is_covered_with_solder_mask: false,
  },
  {
    type: "pcb_plated_hole",
    pcb_plated_hole_id: "pcb_plated_hole_usb_shield",
    pcb_component_id: "pcb_component_usb",
    pcb_port_id: "pcb_port_usb_shield",
    shape: "pill",
    x: 5.5,
    y: 0,
    outer_width: 1.1,
    outer_height: 2,
    hole_width: 0.6,
    hole_height: 1.5,
    ccw_rotation: 180,
    layers: ["top", "bottom"],
    port_hints: ["shield"],
    is_covered_with_solder_mask: false,
  },
] as AnyCircuitElement[]

const getInputProblem = () =>
  convertCircuitJsonToInputProblem(circuitJson, {
    layer: "top",
    subcircuit_connectivity_map_key: "net:GND",
    pad_margin: 0.15,
    trace_margin: 0.15,
  })

const renderSolvedPour = (inputProblem: InputProblem) => {
  const solver = new CopperPourPipelineSolver(inputProblem)
  const output = solver.getOutput()
  const pcbCopperPours = output.brep_shapes.map(
    (brep_shape, index) =>
      ({
        type: "pcb_copper_pour",
        shape: "brep",
        pcb_copper_pour_id: `pcb_copper_pour_${index}`,
        layer: "top",
        source_net_id: "source_net_gnd",
        brep_shape,
        covered_with_solder_mask: true,
      }) as PcbCopperPourBRep,
  )

  return convertCircuitJsonToPcbSvg([...circuitJson, ...pcbCopperPours] as any)
}

test("pill and rotated pill pads clear copper pour", () => {
  const inputProblem = getInputProblem()

  const pillPads = inputProblem.pads.filter((pad) => pad.shape === "pill")

  expect(pillPads).toHaveLength(18)
  expect(
    pillPads.some(
      (pad) =>
        pad.padId === "pcb_smtpad_rotated_pill" && pad.ccwRotation === 90,
    ),
  ).toBe(true)
  expect(
    pillPads.find((pad) => pad.padId === "pcb_plated_hole_usb_shield"),
  ).toMatchObject({
    width: 1.1,
    height: 2,
    radius: 0.55,
    ccwRotation: 180,
  })

  const solver = new CopperPourPipelineSolver(inputProblem)
  const output = solver.getOutput()

  expect(output.brep_shapes).toHaveLength(1)
  expect(output.brep_shapes[0]!.inner_rings).toHaveLength(18)
})

test("pill and rotated pill copper pour svg snapshot", async () => {
  const inputProblem = getInputProblem()

  const svg = renderSolvedPour(inputProblem)

  await expect(svg).toMatchSvgSnapshot(import.meta.path, "pill-pad-copper-pour")
})
