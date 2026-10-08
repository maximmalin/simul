import { expect, test } from "bun:test"
import type { AnyCircuitElement, PcbPlatedHole } from "circuit-json"
import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
} from "lib/index"

// Positions and bounds use board space in mm: +X right, +Y up.
for (const layer of ["top", "bottom"] as const) {
  for (const rotation of [0, 45, 90, 180, 270]) {
    test(`pill rectangular pad clears ${layer} pour at ${rotation} degrees`, () => {
      const hole: PcbPlatedHole = {
        type: "pcb_plated_hole",
        pcb_plated_hole_id: "pill_rect",
        pcb_component_id: "component_0",
        x: 2,
        y: -1,
        layers: ["top", "bottom"],
        pad_shape: "rect",
        hole_width: 2,
        hole_height: 1,
        rect_pad_width: 4,
        rect_pad_height: 2,
        hole_offset_x: 0.2,
        hole_offset_y: 0,
        ...(rotation === 0
          ? { shape: "pill_hole_with_rect_pad", hole_shape: "pill" }
          : {
              shape: "rotated_pill_hole_with_rect_pad",
              hole_shape: "rotated_pill",
              // Pad orientation must not be taken from the drill rotation.
              hole_ccw_rotation: 30,
              rect_ccw_rotation: rotation,
            }),
      }
      const circuitJson: AnyCircuitElement[] = [
        {
          type: "pcb_board",
          pcb_board_id: "board_0",
          center: { x: 0, y: 0 },
          width: 20,
          height: 20,
          thickness: 1.6,
          num_layers: 2,
          material: "fr4",
        },
        {
          type: "source_net",
          source_net_id: "gnd",
          name: "GND",
          subcircuit_connectivity_map_key: "net_gnd",
          member_source_group_ids: [],
          is_ground: true,
          is_power: false,
          is_positive_voltage_source: false,
        },
        hole,
      ]
      const input = convertCircuitJsonToInputProblem(circuitJson, {
        layer,
        source_net_id: "gnd",
        pad_margin: 0.5,
        trace_margin: 0.2,
      })
      expect(input.pads).toEqual([
        {
          padId: "pill_rect",
          layer,
          isPlatedHole: true,
          connectivityKey: "unconnected-plated-hole:pill_rect",
          ...(rotation === 0
            ? { shape: "rect", bounds: { minX: 0, maxX: 4, minY: -2, maxY: 0 } }
            : {
                shape: "rotated_rect",
                x: 2,
                y: -1,
                width: 4,
                height: 2,
                ccwRotation: rotation,
              }),
        },
      ])
      const output = new CopperPourPipelineSolver(input).getOutput()
      expect(output.brep_shapes).toHaveLength(1)
      const voids = output.brep_shapes[0]!.inner_rings
      expect(voids).toHaveLength(1)
      const xs = voids[0]!.vertices.map(({ x }) => x)
      const ys = voids[0]!.vertices.map(({ y }) => y)
      // Expand the rectangle to 5 x 3 mm before rotation (mitered corners).
      // The 45-degree
      // case distinguishes the pad's rotation from the drill's 30 degrees.
      const width =
        rotation === 45 ? 4 * Math.SQRT2 : rotation % 180 === 0 ? 5 : 3
      const height = rotation === 45 ? width : rotation % 180 === 0 ? 3 : 5
      expect(Math.min(...xs)).toBeCloseTo(2 - width / 2, 2)
      expect(Math.max(...xs)).toBeCloseTo(2 + width / 2, 2)
      expect(Math.min(...ys)).toBeCloseTo(-1 - height / 2, 2)
      expect(Math.max(...ys)).toBeCloseTo(-1 + height / 2, 2)
    })
  }
}
