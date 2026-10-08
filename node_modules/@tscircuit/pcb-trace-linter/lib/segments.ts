import type { Point, SimpleRouteJson } from "./types"
export interface TraceSegment {
  traceIndex: number
  segmentIndex: number
  endRouteIndex: number
  pcb_trace_id: string
  connectionName?: string
  layer: string
  width: number
  start: Point
  end: Point
}
/** Via-adjacent wires count; vertical layer changes and through-obstacle jumps do not. */
export function* getTraceSegments(
  srj: SimpleRouteJson,
): Generator<TraceSegment> {
  for (const [traceIndex, trace] of (srj.traces ?? []).entries()) {
    for (let i = 0; i < trace.route.length - 1; i++) {
      const a = trace.route[i]!
      const b = trace.route[i + 1]!
      if (
        (a.route_type !== "wire" && a.route_type !== "via") ||
        (b.route_type !== "wire" && b.route_type !== "via")
      )
        continue
      const fromLayer = a.route_type === "wire" ? a.layer : a.to_layer
      const toLayer = b.route_type === "wire" ? b.layer : b.from_layer
      if (fromLayer !== toLayer) continue
      // Consecutive vias do not describe a planar wire segment.
      if (a.route_type !== "wire" && b.route_type !== "wire") continue
      yield {
        traceIndex,
        segmentIndex: i,
        endRouteIndex: i + 1,
        pcb_trace_id: trace.pcb_trace_id,
        connectionName: trace.connection_name,
        layer: fromLayer,
        width:
          a.route_type === "wire"
            ? a.width
            : b.route_type === "wire"
              ? b.width
              : srj.minTraceWidth,
        start: { x: a.x, y: a.y },
        end: { x: b.x, y: b.y },
      }
    }
  }
}
