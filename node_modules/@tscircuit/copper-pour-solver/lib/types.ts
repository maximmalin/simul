import type { Bounds, Point } from "@tscircuit/math-utils"
import type { BRepShape } from "circuit-json"

export interface InputPourRegion {
  shape: "rect"
  layer: string
  bounds: Bounds
  /**
   * Optional physical PCB boundary in board coordinates (millimetres) used to
   * apply board-edge clearance independently from a custom clipping outline.
   */
  boardEdgeOutline?: Point[]
  outline?: Point[]
  connectivityKey: string
  padMargin: number
  traceMargin: number
  /** Clearance from higher-priority, different-net pour regions. */
  pourMargin?: number
  board_edge_margin?: number
  cutout_margin?: number
  /** Enables thermal reliefs for same-net plated holes and SMT pads. */
  use_thermal_reliefs?: boolean
  /** Width of each thermal relief spoke. Required when enabled. */
  thermal_relief_spoke_width?: number
  /** Number of evenly spaced thermal relief spokes. Defaults to 4. */
  thermal_relief_spoke_count?: number
}

export interface BaseInputPad {
  padId: string
  connectivityKey: string
  layer: string
  /** Identifies plated holes that can receive thermal reliefs. */
  isPlatedHole?: boolean
  /** Identifies SMT pads that can receive thermal reliefs. */
  isSmtPad?: boolean
}

export interface InputRectPad extends BaseInputPad {
  shape: "rect"
  bounds: Bounds
}

export interface InputRotatedRectPad extends BaseInputPad {
  shape: "rotated_rect"
  x: number
  y: number
  width: number
  height: number
  ccwRotation: number
}

export interface InputCircularPad extends BaseInputPad {
  shape: "circle"
  x: number
  y: number
  radius: number
}

export interface InputPillPad extends BaseInputPad {
  shape: "pill"
  x: number
  y: number
  width: number
  height: number
  radius: number
  ccwRotation: number
}

export interface InputOvalPad extends BaseInputPad {
  shape: "oval"
  x: number
  y: number
  width: number
  height: number
  ccwRotation: number
}

export interface InputTracePad extends BaseInputPad {
  shape: "trace"
  width: number
  segments: Point[]
}

export interface InputPolygonPad extends BaseInputPad {
  shape: "polygon"
  points: Point[]
}

export type InputPad =
  | InputRectPad
  | InputRotatedRectPad
  | InputCircularPad
  | InputPillPad
  | InputOvalPad
  | InputTracePad
  | InputPolygonPad

export interface InputProblem {
  /** Later regions have higher priority when different-net regions overlap. */
  regionsForPour: InputPourRegion[]
  pads: InputPad[]
}
export interface PipelineOutput {
  brep_shapes: BRepShape[]
  /** B-Rep shapes aligned with the order of `regionsForPour`. */
  brep_shapes_by_region: BRepShape[][]
}
