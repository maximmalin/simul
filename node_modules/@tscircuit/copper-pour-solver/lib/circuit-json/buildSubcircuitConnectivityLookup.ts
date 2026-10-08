import type { AnyCircuitElement } from "circuit-json"
import type { getFullConnectivityMapFromCircuitJson } from "circuit-json-to-connectivity-map"
import { getElementId } from "@tscircuit/circuit-json-util"
import { getElementSubcircuitConnectivityKey } from "./getElementSubcircuitConnectivityKey"

const getElementSubcircuitId = (
  element: AnyCircuitElement,
): string | undefined => {
  const subcircuitId = (element as any).subcircuit_id
  return typeof subcircuitId === "string" && subcircuitId.length > 0
    ? subcircuitId
    : undefined
}

const getScopedSubcircuitConnectivityKey = (
  subcircuitId: string | undefined,
  subcircuitConnectivityMapKey: string,
): string =>
  subcircuitId
    ? `subcircuit:${subcircuitId}:connectivity:${subcircuitConnectivityMapKey}`
    : subcircuitConnectivityMapKey

const getDescendantSubcircuitIds = (
  circuitJson: AnyCircuitElement[],
  rootSubcircuitId: string | undefined,
): Set<string> | undefined => {
  if (!rootSubcircuitId) return undefined

  const sourceGroupIdToSubcircuitId: Record<string, string> = {}
  for (const element of circuitJson) {
    if (element.type !== "source_group") continue

    const sourceGroupId = (element as any).source_group_id
    const subcircuitId = getElementSubcircuitId(element)
    if (typeof sourceGroupId === "string" && subcircuitId) {
      sourceGroupIdToSubcircuitId[sourceGroupId] = subcircuitId
    }
  }

  const descendantSubcircuitIds = new Set([rootSubcircuitId])
  let changed = true
  while (changed) {
    changed = false
    for (const element of circuitJson) {
      if (element.type !== "source_group") continue

      const subcircuitId = getElementSubcircuitId(element)
      if (!subcircuitId || descendantSubcircuitIds.has(subcircuitId)) continue

      const parentSubcircuitId = (element as any).parent_subcircuit_id
      const parentSourceGroupId = (element as any).parent_source_group_id
      const parentSourceGroupSubcircuitId =
        typeof parentSourceGroupId === "string"
          ? sourceGroupIdToSubcircuitId[parentSourceGroupId]
          : undefined

      if (
        (typeof parentSubcircuitId === "string" &&
          descendantSubcircuitIds.has(parentSubcircuitId)) ||
        (parentSourceGroupSubcircuitId &&
          descendantSubcircuitIds.has(parentSourceGroupSubcircuitId))
      ) {
        descendantSubcircuitIds.add(subcircuitId)
        changed = true
      }
    }
  }

  return descendantSubcircuitIds
}

export const buildSubcircuitConnectivityLookup = (
  circuitJson: AnyCircuitElement[],
  globalConnectivityMap: ReturnType<
    typeof getFullConnectivityMapFromCircuitJson
  >,
  rootSubcircuitId?: string,
) => {
  const descendantSubcircuitIds = getDescendantSubcircuitIds(
    circuitJson,
    rootSubcircuitId,
  )
  const idToSubcircuitConnectivityKey: Record<string, string> = {}
  const idToSubcircuitId: Record<string, string | undefined> = {}
  const scopedKeyToIds: Record<string, string[]> = {}
  const localSubcircuitConnectivityKeys = new Set<string>()

  for (const element of circuitJson) {
    const id = getElementId(element)
    const key = getElementSubcircuitConnectivityKey(element)
    const subcircuitId = getElementSubcircuitId(element)
    if (id) {
      idToSubcircuitId[id] = subcircuitId
    }
    if (
      descendantSubcircuitIds &&
      (!subcircuitId || !descendantSubcircuitIds.has(subcircuitId))
    ) {
      continue
    }

    if (id && key) {
      const scopedKey = getScopedSubcircuitConnectivityKey(subcircuitId, key)
      idToSubcircuitConnectivityKey[id] = scopedKey
      localSubcircuitConnectivityKeys.add(key)
      scopedKeyToIds[scopedKey] ??= []
      scopedKeyToIds[scopedKey].push(id)
    }
  }

  const generatedNetIdToSubcircuitConnectivityKey: Record<string, string> = {}
  for (const [generatedNetId, connectedIds] of Object.entries(
    globalConnectivityMap.netMap,
  )) {
    const connectedSubcircuitKeys = new Set(
      connectedIds
        .map((id) => idToSubcircuitConnectivityKey[id])
        .filter((key): key is string => Boolean(key)),
    )
    const subcircuitKey = Array.from(connectedSubcircuitKeys).sort()[0]
    if (subcircuitKey) {
      generatedNetIdToSubcircuitConnectivityKey[generatedNetId] = subcircuitKey
    }
  }

  const subcircuitConnectivityMap: Record<string, string> = {}
  for (const [scopedKey, ids] of Object.entries(scopedKeyToIds)) {
    const resolvedKeys = new Set<string>()
    for (const id of ids) {
      const generatedNetId = globalConnectivityMap.getNetConnectedToId(id)
      const resolvedKey = generatedNetId
        ? generatedNetIdToSubcircuitConnectivityKey[generatedNetId]
        : undefined
      resolvedKeys.add(resolvedKey ?? scopedKey)
    }

    if (resolvedKeys.size > 1) {
      throw new Error(
        `subcircuit_connectivity_map_key "${scopedKey}" maps to multiple global connectivity keys: ${Array.from(resolvedKeys).join(", ")}`,
      )
    }

    const resolvedKey = resolvedKeys.values().next().value
    if (resolvedKey) {
      subcircuitConnectivityMap[scopedKey] = resolvedKey
    }
  }

  return {
    knownSubcircuitConnectivityKeys: new Set([
      ...Object.keys(scopedKeyToIds),
      ...localSubcircuitConnectivityKeys,
    ]),
    descendantSubcircuitIds,
    getScopedSubcircuitConnectivityKey,
    getElementSubcircuitId,
    resolveSubcircuitConnectivityKey(
      subcircuitConnectivityMapKey: string,
      subcircuitId?: string,
    ): string {
      const matchingScopedKeys = Object.keys(scopedKeyToIds).filter(
        (scopedKey) =>
          scopedKey ===
          getScopedSubcircuitConnectivityKey(
            subcircuitId,
            subcircuitConnectivityMapKey,
          ),
      )

      if (
        subcircuitId &&
        matchingScopedKeys.length === 0 &&
        descendantSubcircuitIds
      ) {
        matchingScopedKeys.push(
          ...Object.keys(scopedKeyToIds).filter((scopedKey) =>
            scopedKey.endsWith(`:connectivity:${subcircuitConnectivityMapKey}`),
          ),
        )
      }

      if (!subcircuitId) {
        matchingScopedKeys.push(
          ...Object.keys(scopedKeyToIds).filter((scopedKey) =>
            scopedKey.endsWith(`:connectivity:${subcircuitConnectivityMapKey}`),
          ),
        )
      }

      const uniqueMatchingScopedKeys = Array.from(new Set(matchingScopedKeys))
      if (uniqueMatchingScopedKeys.length === 0) {
        if (subcircuitId && descendantSubcircuitIds) {
          throw new Error(
            `No subcircuit_connectivity_map_key "${subcircuitConnectivityMapKey}" found in subcircuit "${subcircuitId}" or its child subcircuits.`,
          )
        }

        return getScopedSubcircuitConnectivityKey(
          subcircuitId,
          subcircuitConnectivityMapKey,
        )
      }
      if (uniqueMatchingScopedKeys.length > 1) {
        throw new Error(
          `subcircuit_connectivity_map_key "${subcircuitConnectivityMapKey}" exists in multiple subcircuits. Pass subcircuit_id to disambiguate.`,
        )
      }

      return (
        subcircuitConnectivityMap[uniqueMatchingScopedKeys[0]!] ??
        uniqueMatchingScopedKeys[0]!
      )
    },
    getSubcircuitConnectivityKeyForId(id: string): string | undefined {
      if (descendantSubcircuitIds) {
        const subcircuitId = idToSubcircuitId[id]
        if (!subcircuitId || !descendantSubcircuitIds.has(subcircuitId)) {
          return undefined
        }
      }

      const directKey = idToSubcircuitConnectivityKey[id]
      const generatedNetId = globalConnectivityMap.getNetConnectedToId(id)
      if (!generatedNetId) {
        return directKey
          ? (subcircuitConnectivityMap[directKey] ?? directKey)
          : undefined
      }

      return (
        generatedNetIdToSubcircuitConnectivityKey[generatedNetId] ??
        (directKey ? subcircuitConnectivityMap[directKey] : undefined) ??
        directKey
      )
    },
  }
}
