import type { AnyCircuitElement } from "circuit-json"

export const getElementSubcircuitConnectivityKey = (
  element: AnyCircuitElement,
): string | undefined => {
  const key = (element as any).subcircuit_connectivity_map_key
  return typeof key === "string" && key.length > 0 ? key : undefined
}
