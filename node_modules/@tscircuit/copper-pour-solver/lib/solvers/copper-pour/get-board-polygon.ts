import type { InputPourRegion } from "lib/types"
import { normalizeRing, type PolygonRing } from "./polygon-ring"

export const getBoardPolygon = (region: InputPourRegion): PolygonRing => {
  const board_edge_margin = region.board_edge_margin ?? 0

  if (region.outline && region.outline.length > 0) {
    return normalizeRing(region.outline, "getBoardPolygon.outline")
  }

  const { bounds } = region
  const newBounds = {
    minX: bounds.minX + board_edge_margin,
    minY: bounds.minY + board_edge_margin,
    maxX: bounds.maxX - board_edge_margin,
    maxY: bounds.maxY - board_edge_margin,
  }

  if (newBounds.minX >= newBounds.maxX || newBounds.minY >= newBounds.maxY) {
    return []
  }

  return [
    { x: newBounds.minX, y: newBounds.minY },
    { x: newBounds.maxX, y: newBounds.minY },
    { x: newBounds.maxX, y: newBounds.maxY },
    { x: newBounds.minX, y: newBounds.maxY },
  ]
}
