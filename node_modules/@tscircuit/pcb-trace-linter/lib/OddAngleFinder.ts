import { BaseSolver } from "@tscircuit/solver-utils"
import { getTraceSegments, type TraceSegment } from "./segments"
import type { LinterOptions, OddAngleIssue, SimpleRouteJson } from "./types"
import { visualizeIssues } from "./visualizeIssues"

export interface OddAngleFinderParams extends LinterOptions {
  srj: SimpleRouteJson
}
export class OddAngleFinder extends BaseSolver {
  issues: OddAngleIssue[] = []
  private segments: Generator<TraceSegment>
  private tolerance: number
  private minLength: number
  private checked = 0
  private total: number
  constructor(public params: OddAngleFinderParams) {
    super()
    this.tolerance = params.angleToleranceDegrees ?? 0.1
    this.minLength = params.minSegmentLength ?? 1e-9
    if (
      !Number.isFinite(this.tolerance) ||
      this.tolerance < 0 ||
      this.tolerance >= 22.5
    )
      throw new Error("angleToleranceDegrees must be in [0, 22.5).")
    if (!Number.isFinite(this.minLength) || this.minLength < 0)
      throw new Error("minSegmentLength must be finite and nonnegative.")
    this.segments = getTraceSegments(params.srj)
    this.total = (params.srj.traces ?? []).reduce(
      (n, t) => n + Math.max(0, t.route.length - 1),
      0,
    )
    this.MAX_ITERATIONS = this.total + 2
  }
  override _step() {
    const next = this.segments.next()
    if (next.done) {
      this.solved = true
      this.progress = 1
      return
    }
    const s = next.value
    const dx = s.end.x - s.start.x
    const dy = s.end.y - s.start.y
    this.checked++
    if (Math.hypot(dx, dy) > this.minLength) {
      const angleDegrees = ((Math.atan2(dy, dx) * 180) / Math.PI + 360) % 360
      const nearest = Math.round(angleDegrees / 45) * 45
      const deviationDegrees = Math.abs(angleDegrees - nearest)
      if (deviationDegrees > this.tolerance + 1e-10) {
        const { width, ...location } = s
        this.issues.push({
          ...location,
          type: "odd_angle",
          issueId: `odd-angle:${s.traceIndex}:${s.segmentIndex}`,
          location: {
            x: (s.start.x + s.end.x) / 2,
            y: (s.start.y + s.end.y) / 2,
          },
          angleDegrees,
          nearestAllowedAngleDegrees: nearest % 360,
          deviationDegrees,
          message: `${s.pcb_trace_id} segment ${s.segmentIndex} on ${s.layer}: ${angleDegrees.toFixed(2)}° (${deviationDegrees.toFixed(2)}° from a 45° direction)`,
        })
      }
    }
    this.progress = this.checked / Math.max(1, this.total)
    this.stats = {
      segmentsChecked: this.checked,
      issueCount: this.issues.length,
    }
  }
  override getOutput() {
    return this.issues
  }
  override getConstructorParams(): [OddAngleFinderParams] {
    return [this.params]
  }
  override visualize() {
    return visualizeIssues(this.params.srj, this.issues)
  }
}
