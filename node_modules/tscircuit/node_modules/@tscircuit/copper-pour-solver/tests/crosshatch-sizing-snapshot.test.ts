import { expect, test } from "bun:test"
import type { PcbCopperPourBRep } from "circuit-json"
import { convertCircuitJsonToPcbSvg } from "circuit-to-svg"
import { CopperPourPipelineSolver } from "lib"

test("compare default, larger-pitch and wider-copper crosshatch geometry", async () => {
  const cases = [
    { label: "DEFAULT", crosshatchPitch: 1, crosshatchWidth: 0.25 },
    { label: "LARGER PITCH", crosshatchPitch: 2, crosshatchWidth: 0.25 },
    { label: "WIDER COPPER", crosshatchPitch: 2, crosshatchWidth: 0.4 },
  ]
  const panels = cases.map(({ label, crosshatchPitch, crosshatchWidth }, i) => {
    const { brep_shapes } = new CopperPourPipelineSolver({
      regionsForPour: [
        {
          shape: "rect",
          layer: "top",
          bounds: { minX: -5, maxX: 5, minY: -4, maxY: 4 },
          connectivityKey: "net:GND",
          padMargin: 0.2,
          traceMargin: 0.2,
          board_edge_margin: 0.2,
          crosshatch: true,
          crosshatchPitch,
          crosshatchWidth,
        },
      ],
      pads: [],
    }).getOutput()
    const pours: PcbCopperPourBRep[] = brep_shapes.map((brep_shape, j) => ({
      type: "pcb_copper_pour",
      pcb_copper_pour_id: `pour_${i}_${j}`,
      shape: "brep",
      layer: "top",
      covered_with_solder_mask: true,
      brep_shape,
    }))
    // Render identical 10 × 8mm boards at the same scale. Each solve uses the
    // same world origin so only pitch/width change between the panels.
    const pcb = convertCircuitJsonToPcbSvg(
      [
        {
          type: "pcb_board",
          pcb_board_id: `board_${i}`,
          center: { x: 0, y: 0 },
          width: 10,
          height: 8,
          thickness: 1.6,
          num_layers: 2,
          material: "fr4",
        },
        ...pours,
      ],
      { width: 600, height: 500 },
    )
    return `<g transform="translate(${i * 600},0)">
      ${pcb}
      <text x="300" y="520" text-anchor="middle" fill="white" font-family="sans-serif" font-size="22">${label}</text>
      <text x="300" y="550" text-anchor="middle" fill="white" font-family="sans-serif" font-size="18">Pitch ${crosshatchPitch}mm / copper ${crosshatchWidth}mm</text>
    </g>`
  })
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="1800" height="580" viewBox="0 0 1800 580">
    <rect width="1800" height="580" fill="black" />
    ${panels.join("\n")}
  </svg>`
  await expect(svg).toMatchSvgSnapshot(import.meta.path)
})
