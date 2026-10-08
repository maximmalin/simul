import type { FillRule } from "@tscircuit/manifold-2d"
import {
  getCrossSection,
  runManifoldOperation,
  type CrossSection,
} from "./manifold-runtime"
import {
  fromScaledManifoldPolygons,
  MANIFOLD_GEOMETRY_SCALE,
  normalizeRing,
  signedArea,
  toScaledManifoldPolygons,
  type PolygonRing,
} from "./polygon-ring"

export const DEFAULT_MIN_ISLAND_AREA = 1e-8

export type CopperPourIsland = {
  outerRing: PolygonRing
  innerRings: PolygonRing[]
}

const emptyCrossSection = (): CrossSection => getCrossSection().square([0, 0])

export const crossSectionFromPolygon = (
  polygon: PolygonRing,
  fillRule: FillRule = "Positive",
): CrossSection => {
  const scaledPolygons = toScaledManifoldPolygons(
    [polygon],
    "crossSectionFromPolygon",
  )
  const CrossSection = getCrossSection()
  if (scaledPolygons.length === 0) {
    return emptyCrossSection()
  }
  return runManifoldOperation("crossSectionFromPolygon", scaledPolygons, () =>
    CrossSection.ofPolygons(scaledPolygons, fillRule),
  )
}

export const crossSectionFromPolygons = (
  polygons: PolygonRing[],
  fillRule: FillRule = "Positive",
): CrossSection => {
  const scaledPolygons = toScaledManifoldPolygons(
    polygons,
    "crossSectionFromPolygons",
  )
  const CrossSection = getCrossSection()
  if (scaledPolygons.length === 0) {
    return emptyCrossSection()
  }
  return runManifoldOperation("crossSectionFromPolygons", scaledPolygons, () =>
    CrossSection.ofPolygons(scaledPolygons, fillRule),
  )
}

export const composeCrossSections = (
  sections: CrossSection[],
): CrossSection => {
  const nonEmptySections = sections.filter((section) => !section.isEmpty())
  const CrossSection = getCrossSection()
  if (nonEmptySections.length === 0) {
    return emptyCrossSection()
  }
  return runManifoldOperation("composeCrossSections", [], () =>
    CrossSection.compose(nonEmptySections),
  )
}

export const offsetPolygon = (
  polygon: PolygonRing,
  margin: number,
  joinType: "Square" | "Round" | "Miter" = "Miter",
): PolygonRing[] => {
  const scaledPolygons = toScaledManifoldPolygons([polygon], "offsetPolygon")
  if (scaledPolygons.length === 0 || margin <= 0) {
    return scaledPolygons.length === 0 ? [] : [normalizeRing(polygon)]
  }

  const scaledMargin = margin * MANIFOLD_GEOMETRY_SCALE
  const CrossSection = getCrossSection()
  const section = runManifoldOperation(
    "offsetPolygon.input",
    scaledPolygons,
    () => CrossSection.ofPolygons(scaledPolygons, "Positive"),
  )
  const offset = runManifoldOperation(
    "offsetPolygon.offset",
    scaledPolygons,
    () => section.offset(scaledMargin, joinType, 2, 32),
  )
  return fromScaledManifoldPolygons(offset.toPolygons())
}

export const offsetCrossSection = (
  section: CrossSection,
  margin: number,
  joinType: "Square" | "Round" | "Miter" = "Miter",
): CrossSection => {
  if (section.isEmpty() || margin <= 0) return section

  const scaledMargin = margin * MANIFOLD_GEOMETRY_SCALE
  const polygons = section.toPolygons()
  return runManifoldOperation("offsetCrossSection", polygons, () =>
    section.offset(scaledMargin, joinType, 2, 32),
  )
}

export const subtractCrossSectionBlockers = (
  pourSection: CrossSection,
  blockerSections: CrossSection[],
): CrossSection => {
  const blockerSection = composeCrossSections(blockerSections)
  if (pourSection.isEmpty() || blockerSection.isEmpty()) return pourSection

  return runManifoldOperation(
    "subtractCrossSectionBlockers",
    [...pourSection.toPolygons(), ...blockerSection.toPolygons()],
    () => pourSection.subtract(blockerSection),
  )
}

export const subtractBlockersFromPour = (
  pourPolygon: PolygonRing,
  blockerPolygons: PolygonRing[],
): CrossSection => {
  const pourSection = crossSectionFromPolygon(pourPolygon)
  const blockerSection = crossSectionFromPolygons(blockerPolygons)

  if (pourSection.isEmpty() || blockerSection.isEmpty()) {
    return pourSection
  }

  const operationPolygons = [
    ...toScaledManifoldPolygons([pourPolygon], "subtractBlockersFromPour.pour"),
    ...toScaledManifoldPolygons(
      blockerPolygons,
      "subtractBlockersFromPour.blockers",
    ),
  ]

  return runManifoldOperation(
    "subtractBlockersFromPour",
    operationPolygons,
    () => pourSection.subtract(blockerSection),
  )
}

export const removeTinyIslands = (
  section: CrossSection,
  minArea = DEFAULT_MIN_ISLAND_AREA,
): CrossSection => {
  if (section.isEmpty()) return section

  const minScaledArea =
    minArea * MANIFOLD_GEOMETRY_SCALE * MANIFOLD_GEOMETRY_SCALE
  const islands = section
    .decompose()
    .filter((island) => Math.abs(island.area()) >= minScaledArea)

  return composeCrossSections(islands)
}

export const crossSectionToCopperPourIslands = (
  section: CrossSection,
): CopperPourIsland[] => {
  const islands: CopperPourIsland[] = []

  for (const island of section.decompose()) {
    const rings = fromScaledManifoldPolygons(island.toPolygons())
    if (rings.length === 0) continue

    const outerRing = rings.reduce((largest, ring) =>
      Math.abs(signedArea(ring)) > Math.abs(signedArea(largest))
        ? ring
        : largest,
    )
    const innerRings = rings.filter((ring) => ring !== outerRing)

    islands.push({
      outerRing,
      innerRings,
    })
  }

  return islands
}
