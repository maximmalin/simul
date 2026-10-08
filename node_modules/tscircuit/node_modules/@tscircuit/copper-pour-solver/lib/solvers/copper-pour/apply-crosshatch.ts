import type { InputPad } from "lib/types"
import {
  composeCrossSections,
  crossSectionFromPolygons,
} from "./manifold-geometry-adapter"
import {
  getCrossSection,
  runManifoldOperation,
  type CrossSection,
} from "./manifold-runtime"
import {
  MANIFOLD_GEOMETRY_SCALE,
  type PolygonRing,
  type ScaledPolygons,
} from "./polygon-ring"
import { processObstaclesForPour } from "./process-obstacles"

// Input positions use Manifold's scaled board-world coordinates.
const getCellKey = (x: number, y: number, hatchPitch: number) => {
  const u = ((x + y) * Math.SQRT1_2) / MANIFOLD_GEOMETRY_SCALE
  const v = ((y - x) * Math.SQRT1_2) / MANIFOLD_GEOMETRY_SCALE
  return `${Math.floor(u / hatchPitch)},${Math.floor(v / hatchPitch)}`
}

// Filter each connected fragment separately: a large fragment must not rescue a
// tiny sibling produced when a concave edge cuts the same grid cell in two.
const removeSmallOpenings = (
  openings: CrossSection,
  hatchPitch: number,
  hatchWidth: number,
): CrossSection => {
  // Decomposing thousands of disconnected openings at once can exhaust WASM
  // memory. Each contour stays in one grid cell, so decompose one cell at a time.
  const cells = new Map<string, ScaledPolygons>()
  for (const polygon of openings.toPolygons()) {
    const [x, y] = polygon[0]!
    const key = getCellKey(x, y, hatchPitch)
    const contours = cells.get(key) ?? []
    contours.push(polygon)
    cells.set(key, contours)
  }
  const retained: ScaledPolygons = []
  for (const contours of cells.values()) {
    const cell = getCrossSection().ofPolygons(contours, "Positive")
    const fragments = cell.decompose()
    try {
      for (const fragment of fragments) {
        if (fragment.area() < hatchWidth ** 2 * MANIFOLD_GEOMETRY_SCALE ** 2)
          continue
        const inset = fragment.offset(
          (-hatchWidth / 2) * MANIFOLD_GEOMETRY_SCALE,
          "Round",
        )
        try {
          if (!inset.isEmpty()) retained.push(...fragment.toPolygons())
        } finally {
          inset.delete()
        }
      }
    } finally {
      for (const fragment of fragments) fragment.delete()
      cell.delete()
    }
  }
  return retained.length > 0
    ? getCrossSection().ofPolygons(retained, "Positive")
    : getCrossSection().square([0, 0])
}

/**
 * Clip square openings at the edges of an already-cleared pour. Positions are
 * board-world mm (+X right, +Y up); the 45-degree grid is anchored at (0, 0).
 * Openings leave a hatchWidth solid rim. Cells touching protected pad/trace,
 * thermal, hole, keepout or higher-priority pour clearances are omitted entirely.
 * Partial openings are allowed only at pour edges; tiny fragments are discarded.
 */
export const applyCrosshatch = ({
  solidPour,
  hatchPitch = 1,
  hatchWidth = 0.25,
  padsForLayer,
  connectivityKey,
  obstaclePolygons,
  higherPriorityPourBlockers,
}: {
  solidPour: CrossSection
  hatchPitch?: number
  hatchWidth?: number
  padsForLayer: InputPad[]
  connectivityKey: string
  obstaclePolygons: PolygonRing[]
  higherPriorityPourBlockers: CrossSection[]
}): CrossSection => {
  if (!Number.isFinite(hatchPitch) || hatchPitch <= 0)
    throw new Error("crosshatchPitch must be a finite positive distance in mm")
  if (
    !Number.isFinite(hatchWidth) ||
    hatchWidth <= 0 ||
    hatchWidth >= hatchPitch
  )
    throw new Error(
      "crosshatchWidth must be positive and less than crosshatchPitch",
    )
  const halfOpening = (hatchPitch - hatchWidth) / 2
  if (solidPour.isEmpty()) return solidPour

  const polygons = solidPour.toPolygons()
  return runManifoldOperation("applyCrosshatch", polygons, () => {
    const interior = solidPour.offset(-hatchWidth * MANIFOLD_GEOMETRY_SCALE)
    const { polygonsToSubtract } = processObstaclesForPour(
      padsForLayer.filter((pad) => pad.connectivityKey === connectivityKey),
      undefined,
      { padMargin: hatchWidth, traceMargin: hatchWidth },
    )
    const protectedCopper = crossSectionFromPolygons(polygonsToSubtract)
    const obstacleClearances = crossSectionFromPolygons(obstaclePolygons)
    const allClearances = composeCrossSections([
      obstacleClearances,
      ...higherPriorityPourBlockers,
    ])
    const expandedClearances = allClearances.offset(
      hatchWidth * MANIFOLD_GEOMETRY_SCALE,
      "Round",
    )
    const protectedAreas = protectedCopper.add(expandedClearances)
    obstacleClearances.delete()
    allClearances.delete()
    expandedClearances.delete()
    protectedCopper.delete()

    try {
      if (interior.isEmpty()) return solidPour

      let minU = Infinity
      let maxU = -Infinity
      let minV = Infinity
      let maxV = -Infinity
      for (const polygon of interior.toPolygons()) {
        for (const [x, y] of polygon) {
          const u = ((x + y) * Math.SQRT1_2) / MANIFOLD_GEOMETRY_SCALE
          const v = ((y - x) * Math.SQRT1_2) / MANIFOLD_GEOMETRY_SCALE
          minU = Math.min(minU, u)
          maxU = Math.max(maxU, u)
          minV = Math.min(minV, v)
          maxV = Math.max(maxV, v)
        }
      }

      const minI = Math.floor(minU / hatchPitch)
      const maxI = Math.floor(maxU / hatchPitch)
      const minJ = Math.floor(minV / hatchPitch)
      const maxJ = Math.floor(maxV / hatchPitch)
      // Bound work before allocating cells, including pitches too small for
      // integer grid indices to advance safely at the board's coordinates.
      if (
        ![minI, maxI, minJ, maxJ].every(Number.isSafeInteger) ||
        (maxI - minI + 1) * (maxJ - minJ + 1) > 1_000_000
      ) {
        throw new Error(
          "Crosshatch exceeds 1,000,000 candidate cells; increase crosshatchPitch or reduce the pour area",
        )
      }
      const openings: Array<{ key: string; ring: PolygonRing }> = []
      for (let i = minI; i <= maxI; i++) {
        for (let j = minJ; j <= maxJ; j++) {
          const u = (i + 0.5) * hatchPitch
          const v = (j + 0.5) * hatchPitch
          openings.push({
            key: `${i},${j}`,
            ring: [
              [u - halfOpening, v - halfOpening],
              [u + halfOpening, v - halfOpening],
              [u + halfOpening, v + halfOpening],
              [u - halfOpening, v + halfOpening],
            ].map(([u, v]) => ({
              x: (u! - v!) * Math.SQRT1_2,
              y: (u! + v!) * Math.SQRT1_2,
            })),
          })
        }
      }

      // Reject whole cells at protected geometry, including tiny obstacles
      // entirely enclosed by a cell. Other cells may be clipped at pour edges.
      const allOpenings = crossSectionFromPolygons(
        openings.map(({ ring }) => ring),
      )
      const conflicts = allOpenings.intersect(protectedAreas)
      allOpenings.delete()
      const rejectedCells = new Set<string>()
      for (const polygon of conflicts.toPolygons()) {
        const [x, y] = polygon[0]!
        rejectedCells.add(getCellKey(x, y, hatchPitch))
      }
      conflicts.delete()

      const acceptedOpenings = crossSectionFromPolygons(
        openings
          .filter(({ key }) => !rejectedCells.has(key))
          .map(({ ring }) => ring),
      )
      const clippedOpenings = acceptedOpenings.intersect(interior)
      acceptedOpenings.delete()
      const filteredOpenings = removeSmallOpenings(
        clippedOpenings,
        hatchPitch,
        hatchWidth,
      )
      clippedOpenings.delete()
      try {
        return solidPour.subtract(filteredOpenings)
      } finally {
        filteredOpenings.delete()
      }
    } finally {
      interior.delete()
      protectedAreas.delete()
    }
  })
}
