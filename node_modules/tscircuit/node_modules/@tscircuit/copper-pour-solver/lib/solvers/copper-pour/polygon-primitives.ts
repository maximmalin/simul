import type { Point } from "@tscircuit/math-utils"
import type { PolygonRing } from "./polygon-ring"

export const circleToPolygon = (
  center: Point,
  radius: number,
  numSegments = 32,
): PolygonRing => {
  const points: PolygonRing = []
  for (let i = 0; i < numSegments; i++) {
    const angle = (i / numSegments) * 2 * Math.PI
    points.push({
      x: center.x + radius * Math.cos(angle),
      y: center.y + radius * Math.sin(angle),
    })
  }
  return points
}

export const ovalToPolygon = (
  center: Point,
  width: number,
  height: number,
  ccwRotation = 0,
  numSegments = 32,
): PolygonRing => {
  if (width <= 0 || height <= 0) return []

  const radiusX = width / 2
  const radiusY = height / 2
  const rotation = (ccwRotation * Math.PI) / 180
  const cosRotation = Math.cos(rotation)
  const sinRotation = Math.sin(rotation)
  const points: PolygonRing = []

  for (let i = 0; i < numSegments; i++) {
    const angle = (i / numSegments) * 2 * Math.PI
    const x = radiusX * Math.cos(angle)
    const y = radiusY * Math.sin(angle)
    points.push({
      x: center.x + x * cosRotation - y * sinRotation,
      y: center.y + x * sinRotation + y * cosRotation,
    })
  }

  return points
}

export const boxToPolygon = (
  minX: number,
  minY: number,
  maxX: number,
  maxY: number,
): PolygonRing => [
  { x: minX, y: minY },
  { x: maxX, y: minY },
  { x: maxX, y: maxY },
  { x: minX, y: maxY },
]

export const rotatedBoxToPolygon = (
  center: Point,
  width: number,
  height: number,
  ccwRotation: number,
): PolygonRing => {
  const halfWidth = width / 2
  const halfHeight = height / 2
  const rotation = (ccwRotation * Math.PI) / 180
  const cosRotation = Math.cos(rotation)
  const sinRotation = Math.sin(rotation)

  return [
    { x: -halfWidth, y: -halfHeight },
    { x: halfWidth, y: -halfHeight },
    { x: halfWidth, y: halfHeight },
    { x: -halfWidth, y: halfHeight },
  ].map((point) => ({
    x: center.x + point.x * cosRotation - point.y * sinRotation,
    y: center.y + point.x * sinRotation + point.y * cosRotation,
  }))
}

export const segmentToPolygon = (
  start: Point,
  end: Point,
  width: number,
): PolygonRing => {
  const segmentLength = Math.hypot(start.x - end.x, start.y - end.y)
  if (segmentLength === 0) return []

  const centerX = (start.x + end.x) / 2
  const centerY = (start.y + end.y) / 2
  const angle = Math.atan2(end.y - start.y, end.x - start.x)
  const cosAngle = Math.cos(angle)
  const sinAngle = Math.sin(angle)
  const halfLength = segmentLength / 2
  const halfWidth = width / 2

  return [
    { x: -halfLength, y: -halfWidth },
    { x: halfLength, y: -halfWidth },
    { x: halfLength, y: halfWidth },
    { x: -halfLength, y: halfWidth },
  ].map((point) => ({
    x: centerX + point.x * cosAngle - point.y * sinAngle,
    y: centerY + point.x * sinAngle + point.y * cosAngle,
  }))
}

export const pillToPolygon = (
  center: Point,
  width: number,
  height: number,
  radius: number,
  ccwRotation = 0,
  numSegments = 32,
): PolygonRing => {
  if (radius <= 0) return []

  const isVertical = height >= width
  const centerlineLength = Math.max(width, height) - 2 * radius
  const halfCenterline = Math.max(0, centerlineLength / 2)
  const baseAngle = isVertical ? Math.PI / 2 : 0
  const rotation = baseAngle + (ccwRotation * Math.PI) / 180

  const cosAngle = Math.cos(rotation)
  const sinAngle = Math.sin(rotation)
  const start = {
    x: center.x - halfCenterline * cosAngle,
    y: center.y - halfCenterline * sinAngle,
  }
  const end = {
    x: center.x + halfCenterline * cosAngle,
    y: center.y + halfCenterline * sinAngle,
  }

  if (halfCenterline === 0) {
    return circleToPolygon(center, radius, numSegments)
  }

  const points: PolygonRing = []
  const halfSegments = Math.max(4, Math.floor(numSegments / 2))

  for (let i = 0; i <= halfSegments; i++) {
    const angle = rotation - Math.PI / 2 + (i / halfSegments) * Math.PI
    points.push({
      x: end.x + radius * Math.cos(angle),
      y: end.y + radius * Math.sin(angle),
    })
  }

  for (let i = 0; i <= halfSegments; i++) {
    const angle = rotation + Math.PI / 2 + (i / halfSegments) * Math.PI
    points.push({
      x: start.x + radius * Math.cos(angle),
      y: start.y + radius * Math.sin(angle),
    })
  }

  return points
}
