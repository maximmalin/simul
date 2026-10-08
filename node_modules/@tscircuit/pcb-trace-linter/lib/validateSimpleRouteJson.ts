import type { SimpleRouteJson } from "./types"

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v)
const finite = (v: unknown): v is number =>
  typeof v === "number" && Number.isFinite(v)
const point = (v: unknown) => isRecord(v) && finite(v.x) && finite(v.y)

export function validateSimpleRouteJson(input: unknown): SimpleRouteJson {
  if (
    !isRecord(input) ||
    !isRecord(input.bounds) ||
    !Array.isArray(input.obstacles) ||
    !Array.isArray(input.connections) ||
    !finite(input.layerCount) ||
    !Number.isInteger(input.layerCount) ||
    input.layerCount < 1 ||
    !finite(input.minTraceWidth) ||
    input.minTraceWidth <= 0
  ) {
    throw new Error(
      "Expected SimpleRouteJson (bounds, layerCount, minTraceWidth, obstacles, connections) or a Circuit JSON array.",
    )
  }
  const b = input.bounds
  if (
    ![b.minX, b.maxX, b.minY, b.maxY].every(finite) ||
    (b.minX as number) > (b.maxX as number) ||
    (b.minY as number) > (b.maxY as number)
  )
    throw new Error("SRJ bounds must be finite and ordered.")
  if (input.traces !== undefined && !Array.isArray(input.traces))
    throw new Error("SRJ traces must be an array.")
  for (const [i, trace] of ((input.traces ?? []) as unknown[]).entries()) {
    if (
      !isRecord(trace) ||
      typeof trace.pcb_trace_id !== "string" ||
      !Array.isArray(trace.route)
    )
      throw new Error(`Trace ${i} requires pcb_trace_id and route.`)
    for (const [j, p] of trace.route.entries()) {
      if (!isRecord(p)) throw new Error(`Invalid route point ${i}:${j}.`)
      const valid =
        p.route_type === "wire"
          ? point(p) &&
            typeof p.layer === "string" &&
            finite(p.width) &&
            p.width > 0
          : p.route_type === "via"
            ? point(p) &&
              typeof p.from_layer === "string" &&
              typeof p.to_layer === "string"
            : p.route_type === "through_obstacle" || p.route_type === "jumper"
              ? point(p.start) && point(p.end)
              : false
      if (!valid)
        throw new Error(
          `Invalid route point ${i}:${j}: expected finite coordinates and a supported route type.`,
        )
    }
  }
  return input as unknown as SimpleRouteJson
}
