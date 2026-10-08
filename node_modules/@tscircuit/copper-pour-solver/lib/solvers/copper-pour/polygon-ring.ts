import type { Point } from "@tscircuit/math-utils"
import type { SimplePolygon } from "@tscircuit/manifold-2d"

export const MANIFOLD_GEOMETRY_SCALE = 1_000_000

export type PolygonRing = Point[]
export type ScaledPolygons = SimplePolygon[]

export const signedArea = (ring: PolygonRing) => {
  let area = 0
  for (let i = 0; i < ring.length; i++) {
    const current = ring[i]!
    const next = ring[(i + 1) % ring.length]!
    area += current.x * next.y - next.x * current.y
  }
  return area / 2
}

export const describeScaledPolygons = (polygons: ScaledPolygons) => {
  let pointCount = 0
  const bbox = {
    minX: Number.POSITIVE_INFINITY,
    minY: Number.POSITIVE_INFINITY,
    maxX: Number.NEGATIVE_INFINITY,
    maxY: Number.NEGATIVE_INFINITY,
  }

  for (const polygon of polygons) {
    pointCount += polygon.length
    for (const [x, y] of polygon) {
      bbox.minX = Math.min(bbox.minX, x)
      bbox.minY = Math.min(bbox.minY, y)
      bbox.maxX = Math.max(bbox.maxX, x)
      bbox.maxY = Math.max(bbox.maxY, y)
    }
  }

  return {
    polygonCount: polygons.length,
    pointCount,
    bbox: pointCount > 0 ? bbox : null,
    scale: MANIFOLD_GEOMETRY_SCALE,
  }
}

const assertFinitePoint = (point: Point, operation: string) => {
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) {
    throw new Error(
      `${operation} received non-finite point (${point.x}, ${point.y})`,
    )
  }
}

const pointsEqual = (a: Point, b: Point) => a.x === b.x && a.y === b.y

export const normalizeRing = (
  ring: PolygonRing,
  operation = "normalizeRing",
): PolygonRing => {
  const normalized: PolygonRing = []

  for (const point of ring) {
    assertFinitePoint(point, operation)
    const roundedPoint = {
      x:
        Math.round(point.x * MANIFOLD_GEOMETRY_SCALE) / MANIFOLD_GEOMETRY_SCALE,
      y:
        Math.round(point.y * MANIFOLD_GEOMETRY_SCALE) / MANIFOLD_GEOMETRY_SCALE,
    }
    const previous = normalized[normalized.length - 1]
    if (!previous || !pointsEqual(previous, roundedPoint)) {
      normalized.push(roundedPoint)
    }
  }

  if (
    normalized.length > 1 &&
    pointsEqual(normalized[0]!, normalized[normalized.length - 1]!)
  ) {
    normalized.pop()
  }

  const uniquePoints = new Set(normalized.map((p) => `${p.x},${p.y}`))
  if (uniquePoints.size < 3 || Math.abs(signedArea(normalized)) < 1e-18) {
    return []
  }

  return normalized
}

export const toScaledManifoldPolygons = (
  polygons: PolygonRing[],
  operation = "toScaledManifoldPolygons",
): ScaledPolygons => {
  const scaledPolygons: ScaledPolygons = []

  for (const polygon of polygons) {
    const normalized = normalizeRing(polygon, operation)
    if (normalized.length < 3) continue
    const positiveRing =
      signedArea(normalized) < 0 ? [...normalized].reverse() : normalized
    scaledPolygons.push(
      positiveRing.map((p) => [
        Math.round(p.x * MANIFOLD_GEOMETRY_SCALE),
        Math.round(p.y * MANIFOLD_GEOMETRY_SCALE),
      ]),
    )
  }

  return scaledPolygons
}

export const fromScaledManifoldPolygons = (
  polygons: SimplePolygon[],
): PolygonRing[] =>
  polygons
    .map((polygon) =>
      normalizeRing(
        polygon.map(([x, y]) => ({
          x: x / MANIFOLD_GEOMETRY_SCALE,
          y: y / MANIFOLD_GEOMETRY_SCALE,
        })),
        "fromScaledManifoldPolygons",
      ),
    )
    .filter((polygon) => polygon.length >= 3)
