import type { SimpleRouteJson } from "@tscircuit/core"
import type { AnyCircuitElement } from "circuit-json"
export type { SimpleRouteJson }
export type LinterInput = SimpleRouteJson | AnyCircuitElement[]
export type Point = { x: number; y: number }
export interface OddAngleIssue {
  type: "odd_angle"
  issueId: string
  message: string
  pcb_trace_id: string
  connectionName?: string
  traceIndex: number
  /** Index of the first endpoint in the original trace.route array. */
  segmentIndex: number
  endRouteIndex: number
  layer: string
  start: Point
  end: Point
  location: Point
  angleDegrees: number
  nearestAllowedAngleDegrees: number
  deviationDegrees: number
}
export type TraceIssue = OddAngleIssue
export interface LinterOptions {
  /** Degrees from a multiple of 45; defaults to 0.1°. */
  angleToleranceDegrees?: number
  /** Ignore duplicate/numerically coincident points; defaults to 1e-9 mm. */
  minSegmentLength?: number
}
