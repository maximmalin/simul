import { getSimpleRouteJsonFromCircuitJson } from "@tscircuit/core"
import type { AnyCircuitElement, PcbTrace } from "circuit-json"
import type { SimpleRouteJson } from "./types"
import { validateSimpleRouteJson } from "./validateSimpleRouteJson"

const layerName = (layer: string | { name: string }): string =>
  typeof layer === "string" ? layer : layer.name

/** Use core's converter for board geometry/connectivity, preserving every supplied PCB trace. */
export function normalizeInput(input: unknown): SimpleRouteJson {
  if (Array.isArray(input)) {
    if (
      !input.every(
        (e) =>
          typeof e === "object" && e !== null && typeof e.type === "string",
      )
    ) {
      throw new Error("Circuit JSON must be an array of elements with a type.")
    }
    const circuitJson = input as AnyCircuitElement[]
    const { simpleRouteJson } = getSimpleRouteJsonFromCircuitJson({
      circuitJson: circuitJson.filter((e) => e.type !== "pcb_trace"),
    })
    // Core filters copper for autorouting. A linter must also inspect standalone/footprint traces.
    const traces = circuitJson
      .filter((e): e is PcbTrace => e.type === "pcb_trace")
      .map((trace) => ({
        type: "pcb_trace" as const,
        pcb_trace_id: trace.pcb_trace_id,
        connection_name: trace.source_trace_id ?? trace.pcb_trace_id,
        route: trace.route.map((p) => {
          if (p.route_type === "wire")
            return { ...p, layer: layerName(p.layer) }
          if (p.route_type === "via")
            return {
              ...p,
              from_layer: layerName(p.from_layer),
              to_layer: layerName(p.to_layer),
            }
          if (p.route_type === "through_pad")
            return {
              ...p,
              route_type: "through_obstacle" as const,
              from_layer: layerName(p.start_layer),
              to_layer: layerName(p.end_layer),
            }
          throw new Error(
            `Unsupported Circuit JSON route type: ${p.route_type}`,
          )
        }),
      }))
    return validateSimpleRouteJson({ ...simpleRouteJson, traces })
  }
  return validateSimpleRouteJson(input)
}
