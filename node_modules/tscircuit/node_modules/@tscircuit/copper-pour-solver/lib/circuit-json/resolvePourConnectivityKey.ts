import type { AnyCircuitElement, SourceNet } from "circuit-json"
import type { buildSubcircuitConnectivityLookup } from "./buildSubcircuitConnectivityLookup"
import type { ConvertCircuitJsonToInputProblemOptions } from "./ConvertCircuitJsonToInputProblemOptions"

type SubcircuitConnectivityLookup = ReturnType<
  typeof buildSubcircuitConnectivityLookup
>

export const resolvePourConnectivityKey = (
  circuitJson: AnyCircuitElement[],
  options: ConvertCircuitJsonToInputProblemOptions,
  subcircuitConnectivityMap: SubcircuitConnectivityLookup,
): string => {
  if (options.subcircuit_connectivity_map_key) {
    return subcircuitConnectivityMap.resolveSubcircuitConnectivityKey(
      options.subcircuit_connectivity_map_key,
      options.subcircuit_id,
    )
  }

  if (options.source_net_id) {
    const sourceNet = circuitJson.find(
      (element): element is SourceNet =>
        element.type === "source_net" &&
        element.source_net_id === options.source_net_id,
    )
    if (!sourceNet) {
      throw new Error(`No source_net found with id "${options.source_net_id}"`)
    }
    if (!sourceNet.subcircuit_connectivity_map_key) {
      throw new Error(
        `source_net "${options.source_net_id}" has no subcircuit_connectivity_map_key`,
      )
    }
    return subcircuitConnectivityMap.resolveSubcircuitConnectivityKey(
      sourceNet.subcircuit_connectivity_map_key,
      sourceNet.subcircuit_id ?? options.subcircuit_id,
    )
  }

  if (options.source_net_name) {
    const sourceNet = circuitJson.find(
      (element): element is SourceNet =>
        element.type === "source_net" &&
        element.name === options.source_net_name &&
        (!options.subcircuit_id ||
          Boolean(
            subcircuitConnectivityMap.descendantSubcircuitIds?.has(
              element.subcircuit_id ?? "",
            ),
          )),
    )
    if (!sourceNet) {
      throw new Error(
        `No source_net found with name "${options.source_net_name}"`,
      )
    }
    if (!sourceNet.subcircuit_connectivity_map_key) {
      throw new Error(
        `source_net "${options.source_net_name}" has no subcircuit_connectivity_map_key`,
      )
    }
    return subcircuitConnectivityMap.resolveSubcircuitConnectivityKey(
      sourceNet.subcircuit_connectivity_map_key,
      sourceNet.subcircuit_id ?? options.subcircuit_id,
    )
  }

  if (options.pour_connectivity_key) {
    if (
      !subcircuitConnectivityMap.knownSubcircuitConnectivityKeys.has(
        options.pour_connectivity_key,
      )
    ) {
      throw new Error(
        `pour_connectivity_key must be a subcircuit_connectivity_map_key. Use subcircuit_connectivity_map_key, source_net_id, or source_net_name instead of a generated connectivity-map id.`,
      )
    }
    return subcircuitConnectivityMap.resolveSubcircuitConnectivityKey(
      options.pour_connectivity_key,
      options.subcircuit_id,
    )
  }

  throw new Error(
    "Copper pour requires source_net_id, source_net_name, or subcircuit_connectivity_map_key",
  )
}
