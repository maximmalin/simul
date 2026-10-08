import type { InputTaperedTracePad } from "lib/types"
import type { PolygonRing } from "./polygon-ring"

/** Sample the actual wire profile, before applying trace clearance. Positions
 * use board-world mm (+X right, +Y up); the normal is a unit direction.
 * Quadratic chord error is bounded to 0.001 mm per side. As in circuit-to-svg,
 * the broad end is squared off; adjacent pads/wires supply junction copper.
 */
export function taperedTraceToPolygon(
  trace: InputTaperedTracePad,
): PolygonRing {
  const { start, end, start_width: w0, end_width: w1 } = trace
  const dx = end.x - start.x
  const dy = end.y - start.y
  const length = Math.hypot(dx, dy)
  if (length === 0 || w0 <= 0 || w1 <= 0) return []
  const delta = Math.abs(w1 - w0)
  const steps =
    trace.width_interpolation_mode === "linear"
      ? 1
      : Math.max(1, Math.ceil(Math.sqrt(delta / 0.008)))
  const left: PolygonRing = []
  const right: PolygonRing = []
  for (let i = 0; i <= steps; i++) {
    const t = i / steps
    const u = w0 <= w1 ? t : 1 - t
    const width =
      trace.width_interpolation_mode === "quadratic"
        ? Math.min(w0, w1) + delta * u * u
        : w0 + (w1 - w0) * t
    const x = start.x + dx * t
    const y = start.y + dy * t
    left.push({
      x: x - ((dy / length) * width) / 2,
      y: y + ((dx / length) * width) / 2,
    })
    right.push({
      x: x + ((dy / length) * width) / 2,
      y: y - ((dx / length) * width) / 2,
    })
  }
  // Counterclockwise for the solver's positive fill rule.
  return [...right, ...left.reverse()]
}
