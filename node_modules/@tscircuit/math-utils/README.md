# @tscircuit/math-utils

This repository contains a collection of TypeScript utility functions for geometric calculations, primarily focused on line intersection and distance calculations.

## Features

- Line intersection detection
- Segment intersection detection
- Segment-rectangle intersection detection
- Point-to-segment distance calculation
- Orientation of points
- Distance between points
- Bounds overlap and distance calculations

## Installation

```bash
bun add @tscircuit/math-utils
```

## Usage

Import the functions you need in your TypeScript project:

```typescript
import {
  doesLineIntersectLine,
  doSegmentsIntersect,
  pointToSegmentDistance,
} from "./src/index"

// Example usage
const point1 = { x: 0, y: 0 }
const point2 = { x: 5, y: 5 }
const point3 = { x: 0, y: 5 }
const point4 = { x: 5, y: 0 }

const intersects = doesLineIntersectLine([point1, point2], [point3, point4])
console.log("Lines intersect:", intersects)
```

## API Reference

| Function | Description |
| -------- | ----------- |
| [`doesLineIntersectLine(line1, line2, options?)`](./src/line-intersections.ts) | Determine if two lines intersect, optionally considering line thickness. |
| [`doSegmentsIntersect(p1, q1, p2, q2)`](./src/line-intersections.ts) | Check if two line segments intersect. |
| [`doesSegmentIntersectRect(a, b, rect)`](./src/line-intersections.ts) | Check if a segment intersects an axis-aligned rectangle. |
| [`orientation(p, q, r)`](./src/line-intersections.ts) | Calculate the orientation of three points. |
| [`onSegment(p, q, r)`](./src/line-intersections.ts) | Determine if point `q` lies on the segment `p`–`r`. |
| [`pointToSegmentDistance(p, v, w)`](./src/line-intersections.ts) | Minimum distance between a point and a segment. |
| [`distance(p1, p2)`](./src/line-intersections.ts) | Euclidean distance between two points. |
| [`getSegmentIntersection(a, b, u, v)`](./src/line-intersections.ts) | Intersection point of two segments or `null` if none. |
| [`getBoundingBox(box)`](./src/nearest-box.ts) | Compute the bounding box of a box. |
| [`computeManhattanDistanceBetweenBoxes(boxA, boxB)`](./src/nearest-box.ts) | Minimum Manhattan distance between two boxes and the nearest points. |
| [`clamp(value, min, max)`](./src/nearest-box.ts) | Clamp a value between `min` and `max`. |
| [`findNearestPointsBetweenBoxSets(setA, setB)`](./src/nearest-box.ts) | Find nearest points between two sets of boxes. |
| [`getUnitVectorFromPointAToB(a, b)`](./src/get-unit-vector.ts) | Unit vector pointing from point A to B. |
| [`getUnitVectorFromDirection(direction)`](./src/get-unit-vector.ts) | Unit vector for the given cardinal direction. |
| [`grid(options)`](./src/grid.ts) | Generate grid cell positions with spacing and offsets. |
| [`segmentToSegmentMinDistance(a, b, u, v)`](./src/segment-distance.ts) | Minimum distance between two line segments. |
| [`segmentToBoundsMinDistance(a, b, bounds)`](./src/segment-distance.ts) | Minimum distance from a segment to a bounds rectangle. |
| [`segmentToBoxMinDistance(a, b, box)`](./src/segment-distance.ts) | Minimum distance from a segment to a box. |
| [`segmentToCircleMinDistance(a, b, circle)`](./src/segment-distance.ts) | Minimum distance from a segment to a circle. |
| [`pointToSegmentClosestPoint(p, a, b)`](./src/segment-distance.ts) | Closest point on a segment to the given point. |
| [`pointToBoxDistance(p, box)`](./src/point-distance.ts) | Minimum distance from a point to a box. |
| [`pointToBoundsDistance(p, bounds)`](./src/point-distance.ts) | Minimum distance from a point to a bounds rectangle. |
| [`midpoint(p1, p2)`](./src/point-distance.ts) | Midpoint between two points. |
| [`distSq(p1, p2)`](./src/point-distance.ts) | Squared distance between two points. |
| [`range(start, end?, step?)`](./src/range.ts) | Create an array of numbers progressing from start up to, but not including, end. |
| [`doBoundsOverlap(bounds1, bounds2)`](./src/bounds-overlap.ts) | Determine if two bounding rectangles overlap. |
| [`boundsAreaOverlap(bounds1, bounds2)`](./src/bounds-area-overlap.ts) | Area of overlap between two bounding rectangles. |
| [`boundsDistance(bounds1, bounds2)`](./src/bounds-distance.ts) | Minimum distance between two bounding rectangles. |
| [`isPointInsidePolygon(point, polygon)`](./src/polygon.ts) | Determine if a point lies inside a polygon. |
| [`areBoundsOverlappingPolygon(bounds, polygon)`](./src/polygon.ts) | Check whether bounds intersect or are contained by a polygon. |
| [`areBoundsCompletelyInsidePolygon(bounds, polygon)`](./src/polygon.ts) | Determine if bounds are fully contained within a polygon. |
| [`isRectOverlappingPolygon(rect, polygon)`](./src/polygon.ts) | Check whether a rectangle defined by a center point with dimensions or by bounds overlaps a polygon. |
| [`isRectCompletelyInsidePolygon(rect, polygon)`](./src/polygon.ts) | Determine if a rectangle defined by a center point with dimensions or by bounds is fully contained within a polygon. |

`rect` objects passed to these helpers can either specify a `center` along with `width` and `height` or provide `Bounds` directly.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the [MIT License](LICENSE).
