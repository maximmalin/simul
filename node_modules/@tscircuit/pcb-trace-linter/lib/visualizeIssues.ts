import type { GraphicsObject } from "graphics-debug"
import { getTraceSegments } from "./segments"
import type { SimpleRouteJson, TraceIssue } from "./types"

export function visualizeIssues(
  srj: SimpleRouteJson,
  issues: TraceIssue[],
): GraphicsObject {
  const span = Math.max(
    srj.bounds.maxX - srj.bounds.minX,
    srj.bounds.maxY - srj.bounds.minY,
    1,
  )
  return {
    coordinateSystem: "cartesian",
    title: `PCB trace lint — ${issues.length} odd-angle segments`,
    rects: srj.obstacles.map((o) => ({
      center: o.center,
      width: o.width,
      height: o.height,
      fill: "rgba(148, 163, 184, 0.18)",
      stroke: "#cbd5e1",
    })),
    lines: [
      ...Array.from(getTraceSegments(srj), (s) => ({
        points: [s.start, s.end],
        strokeColor: s.layer === "top" ? "#64748b" : "#a5b4fc",
        strokeWidth: s.width,
      })),
      ...issues.map((issue, i) => ({
        points: [issue.start, issue.end],
        strokeColor: "#e11d48",
        strokeWidth: Math.max(srj.minTraceWidth * 1.7, span / 450),
        label: `#${i + 1}: ${issue.message}`,
      })),
    ],
    points: issues.map((issue, i) => ({
      ...issue.location,
      color: "#be123c",
      label: `#${i + 1}`,
      pointId: issue.issueId,
    })),
  }
}
