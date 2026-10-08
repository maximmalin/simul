import type { Point } from "@tscircuit/math-utils"
import type {
  InputCircularPad,
  InputOvalPad,
  InputPad,
  InputPillPad,
  InputPolygonPad,
  InputRectPad,
  InputRotatedRectPad,
  InputTracePad,
} from "lib/types"
import { generateThermalReliefClearances } from "./generate-thermal-relief-clearances"
import { offsetPolygon } from "./manifold-geometry-adapter"
import {
  boxToPolygon,
  circleToPolygon,
  ovalToPolygon,
  pillToPolygon,
  rotatedBoxToPolygon,
  segmentToPolygon,
} from "./polygon-primitives"
import { normalizeRing, type PolygonRing } from "./polygon-ring"

interface ProcessedObstacles {
  polygonsToSubtract: PolygonRing[]
}

const isRectPad = (pad: InputPad): pad is InputRectPad => pad.shape === "rect"
const isRotatedRectPad = (pad: InputPad): pad is InputRotatedRectPad =>
  pad.shape === "rotated_rect"
const isTracePad = (pad: InputPad): pad is InputTracePad =>
  pad.shape === "trace"
const isCircularPad = (pad: InputPad): pad is InputCircularPad =>
  pad.shape === "circle"
const isPillPad = (pad: InputPad): pad is InputPillPad => pad.shape === "pill"
const isOvalPad = (pad: InputPad): pad is InputOvalPad => pad.shape === "oval"
const isPolygonPad = (pad: InputPad): pad is InputPolygonPad =>
  pad.shape === "polygon"

export const processObstaclesForPour = (
  pads: InputPad[],
  pourConnectivityKey: string,
  margins: {
    padMargin: number
    traceMargin: number
    board_edge_margin?: number
    cutoutMargin?: number
    use_thermal_reliefs?: boolean
    thermal_relief_spoke_width?: number
    thermal_relief_spoke_count?: number
  },
  boardOutline?: Point[],
): ProcessedObstacles => {
  const polygonsToSubtract: PolygonRing[] = []

  const {
    padMargin,
    traceMargin,
    board_edge_margin,
    cutoutMargin,
    use_thermal_reliefs,
    thermal_relief_spoke_width,
    thermal_relief_spoke_count,
  } = margins

  if (
    boardOutline &&
    boardOutline.length > 0 &&
    board_edge_margin &&
    board_edge_margin > 0
  ) {
    const vertices = normalizeRing(
      boardOutline,
      "processObstacles.boardOutline",
    )

    // Add clearance shapes at vertices
    for (let i = 0; i < vertices.length; i++) {
      const p1 = vertices[i === 0 ? vertices.length - 1 : i - 1]
      const p2 = vertices[i]
      const p3 = vertices[(i + 1) % vertices.length]

      if (!p1 || !p2 || !p3) continue

      const v1 = { x: p2.x - p1.x, y: p2.y - p1.y }
      const v2 = { x: p3.x - p2.x, y: p3.y - p2.y }
      const crossProduct = v1.x * v2.y - v1.y * v2.x

      polygonsToSubtract.push(circleToPolygon(p2, board_edge_margin))

      if (crossProduct < 0) {
        polygonsToSubtract.push(
          boxToPolygon(
            p2.x - board_edge_margin,
            p2.y - board_edge_margin,
            p2.x + board_edge_margin,
            p2.y + board_edge_margin,
          ),
        )
      }
    }

    // Add rectangles for each segment to create clearance along edges
    for (let i = 0; i < vertices.length; i++) {
      const p1 = vertices[i]
      const p2 = vertices[(i + 1) % vertices.length]

      if (!p1 || !p2) continue

      const segmentPolygon = segmentToPolygon(p1, p2, board_edge_margin * 2)
      if (segmentPolygon.length > 0) {
        polygonsToSubtract.push(segmentPolygon)
      }
    }
  }

  for (const pad of pads) {
    const isOnNet = pad.connectivityKey === pourConnectivityKey

    if (isOnNet) {
      if ((pad.isPlatedHole || pad.isSmtPad) && use_thermal_reliefs) {
        polygonsToSubtract.push(
          ...generateThermalReliefClearances(
            pad,
            padMargin,
            thermal_relief_spoke_width,
            thermal_relief_spoke_count,
          ),
        )
      }
      continue
    }

    const isHoleOrCutout =
      pad.connectivityKey.startsWith("hole:") ||
      pad.connectivityKey.startsWith("cutout:")
    const isKeepout = pad.connectivityKey.startsWith("keepout:")
    const getMargin = (defaultMargin: number) =>
      isKeepout ? 0 : isHoleOrCutout ? (cutoutMargin ?? 0) : defaultMargin

    if (isCircularPad(pad)) {
      const margin = getMargin(padMargin)
      polygonsToSubtract.push(
        circleToPolygon({ x: pad.x, y: pad.y }, pad.radius + margin),
      )
      continue
    }

    if (isPillPad(pad)) {
      const margin = getMargin(padMargin)
      polygonsToSubtract.push(
        pillToPolygon(
          { x: pad.x, y: pad.y },
          pad.width + margin * 2,
          pad.height + margin * 2,
          pad.radius + margin,
          pad.ccwRotation,
        ),
      )
      continue
    }

    if (isOvalPad(pad)) {
      const margin = getMargin(padMargin)
      const polygon = ovalToPolygon(
        { x: pad.x, y: pad.y },
        pad.width,
        pad.height,
        pad.ccwRotation,
      )
      polygonsToSubtract.push(
        ...(margin > 0 ? offsetPolygon(polygon, margin, "Round") : [polygon]),
      )
      continue
    }

    if (isRectPad(pad)) {
      const margin = getMargin(padMargin)
      const { bounds } = pad
      polygonsToSubtract.push(
        boxToPolygon(
          bounds.minX - margin,
          bounds.minY - margin,
          bounds.maxX + margin,
          bounds.maxY + margin,
        ),
      )
      continue
    }

    if (isRotatedRectPad(pad)) {
      const margin = getMargin(padMargin)
      polygonsToSubtract.push(
        rotatedBoxToPolygon(
          { x: pad.x, y: pad.y },
          pad.width + margin * 2,
          pad.height + margin * 2,
          pad.ccwRotation,
        ),
      )
      continue
    }

    if (isPolygonPad(pad)) {
      const margin = getMargin(padMargin)

      const seen = new Set<string>()
      const uniquePoints = pad.points.filter((p) => {
        const key = `${p.x},${p.y}`
        if (seen.has(key)) {
          return false
        }
        seen.add(key)
        return true
      })

      if (uniquePoints.length < 3) continue

      const polygon = normalizeRing(uniquePoints, "processObstacles.polygonPad")
      if (polygon.length < 3) continue

      if (margin <= 0) {
        polygonsToSubtract.push(polygon)
        continue
      }

      polygonsToSubtract.push(...offsetPolygon(polygon, margin))
      continue
    }

    if (isTracePad(pad)) {
      // Add circles for each vertex
      for (const segment of pad.segments) {
        polygonsToSubtract.push(
          circleToPolygon(segment, pad.width / 2 + traceMargin),
        )
      }

      // Add rectangles for each segment
      for (let i = 0; i < pad.segments.length - 1; i++) {
        const p1 = pad.segments[i]
        const p2 = pad.segments[i + 1]

        if (!p1 || !p2) continue

        const segmentPolygon = segmentToPolygon(
          p1,
          p2,
          pad.width + traceMargin * 2,
        )
        if (segmentPolygon.length > 0) {
          polygonsToSubtract.push(segmentPolygon)
        }
      }
    }
  }

  return { polygonsToSubtract }
}
