import type {
  AnyCircuitElement,
  PCBKeepout,
  PcbBoard,
  PcbPlatedHole,
  PcbSmtPad,
  PcbTrace,
  PcbVia,
  Point,
} from "circuit-json"
import { getFullConnectivityMapFromCircuitJson } from "circuit-json-to-connectivity-map"
import type {
  InputCircularPad,
  InputOvalPad,
  InputPad,
  InputPillPad,
  InputPolygonPad,
  InputProblem,
  InputRectPad,
  InputRotatedRectPad,
  InputTracePad,
} from "lib/types"
import { buildSubcircuitConnectivityLookup } from "./buildSubcircuitConnectivityLookup"
import type { ConvertCircuitJsonToInputProblemOptions } from "./ConvertCircuitJsonToInputProblemOptions"
import { resolvePourConnectivityKey } from "./resolvePourConnectivityKey"

export const convertCircuitJsonToInputProblem = (
  circuitJson: AnyCircuitElement[],
  optionsOrOptions:
    | ConvertCircuitJsonToInputProblemOptions
    | ConvertCircuitJsonToInputProblemOptions[],
): InputProblem => {
  if (Array.isArray(optionsOrOptions)) {
    if (optionsOrOptions.length === 0) {
      return { pads: [], regionsForPour: [] }
    }

    const subcircuitId = optionsOrOptions[0]!.subcircuit_id
    if (
      optionsOrOptions.some((options) => options.subcircuit_id !== subcircuitId)
    ) {
      throw new Error(
        "All copper pours in one input problem must use the same subcircuit_id",
      )
    }

    const inputProblems = optionsOrOptions.map((options) =>
      convertCircuitJsonToInputProblem(circuitJson, options),
    )
    const padsByLayerAndId = new Map<string, InputPad>()
    for (const inputProblem of inputProblems) {
      for (const pad of inputProblem.pads) {
        padsByLayerAndId.set(`${pad.layer}:${pad.padId}`, pad)
      }
    }

    return {
      pads: [...padsByLayerAndId.values()],
      regionsForPour: inputProblems.flatMap(
        (inputProblem) => inputProblem.regionsForPour,
      ),
    }
  }

  const options = optionsOrOptions
  const pcb_board = circuitJson.find((e) => e.type === "pcb_board") as
    | PcbBoard
    | undefined

  if (!pcb_board) throw new Error("No pcb_board found in circuit json")

  const globalConnectivityMap =
    getFullConnectivityMapFromCircuitJson(circuitJson)
  const subcircuitConnectivityMap = buildSubcircuitConnectivityLookup(
    circuitJson,
    globalConnectivityMap,
    options.subcircuit_id,
  )
  const pourConnectivityKey = resolvePourConnectivityKey(
    circuitJson,
    options,
    subcircuitConnectivityMap,
  )
  const { getSubcircuitConnectivityKeyForId } = subcircuitConnectivityMap

  const pads: InputPad[] = []

  for (const elm of circuitJson) {
    if (elm.type === "pcb_smtpad") {
      const smtpad = elm as PcbSmtPad
      if (smtpad.layer !== options.layer) continue

      let connectivityKey: string | undefined
      connectivityKey = getSubcircuitConnectivityKeyForId(smtpad.pcb_smtpad_id)
      if (!connectivityKey) {
        connectivityKey = `unconnected:${smtpad.pcb_smtpad_id}`
      }

      if (smtpad.shape === "rect") {
        pads.push({
          shape: "rect",
          padId: smtpad.pcb_smtpad_id,
          layer: smtpad.layer,
          connectivityKey,
          isSmtPad: true,
          bounds: {
            minX: smtpad.x - smtpad.width! / 2,
            minY: smtpad.y - smtpad.height! / 2,
            maxX: smtpad.x + smtpad.width! / 2,
            maxY: smtpad.y + smtpad.height! / 2,
          },
        } as InputRectPad)
      } else if (smtpad.shape === "rotated_rect") {
        pads.push({
          shape: "rotated_rect",
          padId: smtpad.pcb_smtpad_id,
          layer: smtpad.layer,
          connectivityKey,
          isSmtPad: true,
          x: smtpad.x,
          y: smtpad.y,
          width: smtpad.width,
          height: smtpad.height,
          ccwRotation: smtpad.ccw_rotation,
        } as InputRotatedRectPad)
      } else if (smtpad.shape === "circle") {
        pads.push({
          shape: "circle",
          padId: smtpad.pcb_smtpad_id,
          layer: smtpad.layer,
          connectivityKey,
          isSmtPad: true,
          x: smtpad.x,
          y: smtpad.y,
          radius: smtpad.radius!,
        } as InputCircularPad)
      } else if (smtpad.shape === "pill" || smtpad.shape === "rotated_pill") {
        pads.push({
          shape: "pill",
          padId: smtpad.pcb_smtpad_id,
          layer: smtpad.layer,
          connectivityKey,
          isSmtPad: true,
          x: smtpad.x,
          y: smtpad.y,
          width: smtpad.width!,
          height: smtpad.height!,
          radius: smtpad.radius!,
          ccwRotation:
            smtpad.shape === "rotated_pill" ? smtpad.ccw_rotation : 0,
        } as InputPillPad)
      } else if (smtpad.shape === "polygon") {
        pads.push({
          shape: "polygon",
          padId: smtpad.pcb_smtpad_id,
          layer: smtpad.layer,
          connectivityKey,
          isSmtPad: true,
          points: smtpad.points,
        } as InputPolygonPad)
      }
    } else if (elm.type === "pcb_plated_hole") {
      const platedHole = elm as PcbPlatedHole
      if (!platedHole.layers.includes(options.layer)) continue

      let connectivityKey = getSubcircuitConnectivityKeyForId(
        platedHole.pcb_plated_hole_id,
      )
      if (!connectivityKey) {
        connectivityKey = `unconnected-plated-hole:${platedHole.pcb_plated_hole_id}`
      }

      if (platedHole.shape === "hole_with_polygon_pad") {
        const ccwRotationRadians =
          ((platedHole.ccw_rotation ?? 0) * Math.PI) / 180
        const cosRotation = Math.cos(ccwRotationRadians)
        const sinRotation = Math.sin(ccwRotationRadians)
        pads.push({
          shape: "polygon",
          padId: platedHole.pcb_plated_hole_id,
          layer: options.layer,
          connectivityKey,
          isPlatedHole: true,
          points: platedHole.pad_outline.map((vertex) => ({
            x: platedHole.x + vertex.x * cosRotation - vertex.y * sinRotation,
            y: platedHole.y + vertex.x * sinRotation + vertex.y * cosRotation,
          })),
        })
      } else if (platedHole.shape === "circle") {
        pads.push({
          shape: "circle",
          padId: platedHole.pcb_plated_hole_id,
          layer: options.layer,
          connectivityKey,
          isPlatedHole: true,
          x: platedHole.x,
          y: platedHole.y,
          radius: platedHole.outer_diameter / 2,
        } as InputCircularPad)
      } else if (platedHole.shape === "pill") {
        pads.push({
          shape: "pill",
          padId: platedHole.pcb_plated_hole_id,
          layer: options.layer,
          connectivityKey,
          isPlatedHole: true,
          x: platedHole.x,
          y: platedHole.y,
          width: platedHole.outer_width,
          height: platedHole.outer_height,
          radius: Math.min(platedHole.outer_width, platedHole.outer_height) / 2,
          ccwRotation: platedHole.ccw_rotation,
        } as InputPillPad)
      } else if (platedHole.shape === "oval") {
        pads.push({
          shape: "oval",
          padId: platedHole.pcb_plated_hole_id,
          layer: options.layer,
          connectivityKey,
          isPlatedHole: true,
          x: platedHole.x,
          y: platedHole.y,
          width: platedHole.outer_width,
          height: platedHole.outer_height,
          ccwRotation: platedHole.ccw_rotation,
        } as InputOvalPad)
      } else if (
        platedHole.shape === "circular_hole_with_rect_pad" ||
        platedHole.shape === "pill_hole_with_rect_pad" ||
        platedHole.shape === "rotated_pill_hole_with_rect_pad"
      ) {
        const rectWidth = platedHole.rect_pad_width
        const rectHeight = platedHole.rect_pad_height
        if (typeof rectWidth !== "number" || typeof rectHeight !== "number") {
          continue
        }
        const rectRotation =
          platedHole.shape === "pill_hole_with_rect_pad"
            ? 0
            : (platedHole.rect_ccw_rotation ?? 0)
        if (rectRotation === 0) {
          pads.push({
            shape: "rect",
            padId: platedHole.pcb_plated_hole_id,
            layer: options.layer,
            connectivityKey,
            isPlatedHole: true,
            bounds: {
              minX: platedHole.x - rectWidth / 2,
              minY: platedHole.y - rectHeight / 2,
              maxX: platedHole.x + rectWidth / 2,
              maxY: platedHole.y + rectHeight / 2,
            },
          } as InputRectPad)
        } else {
          pads.push({
            shape: "rotated_rect",
            padId: platedHole.pcb_plated_hole_id,
            layer: options.layer,
            connectivityKey,
            isPlatedHole: true,
            x: platedHole.x,
            y: platedHole.y,
            width: rectWidth,
            height: rectHeight,
            ccwRotation: rectRotation,
          } as InputRotatedRectPad)
        }
      }
    } else if (elm.type === "pcb_hole") {
      const hole = elm
      if (hole.hole_shape === "rect") {
        pads.push({
          shape: "rect",
          padId: hole.pcb_hole_id,
          layer: options.layer,
          connectivityKey: `hole:${hole.pcb_hole_id}`,
          bounds: {
            minX: hole.x - hole.hole_width / 2,
            minY: hole.y - hole.hole_height / 2,
            maxX: hole.x + hole.hole_width / 2,
            maxY: hole.y + hole.hole_height / 2,
          },
        })
        continue
      }
      if (hole.hole_shape === "pill" || hole.hole_shape === "rotated_pill") {
        let ccwRotation = 0
        if (hole.hole_shape === "rotated_pill") ccwRotation = hole.ccw_rotation
        pads.push({
          shape: "pill",
          padId: hole.pcb_hole_id,
          layer: options.layer,
          connectivityKey: `hole:${hole.pcb_hole_id}`,
          x: hole.x,
          y: hole.y,
          width: hole.hole_width,
          height: hole.hole_height,
          radius: Math.min(hole.hole_width, hole.hole_height) / 2,
          ccwRotation,
        })
        continue
      }
      if (hole.hole_shape !== "circle") continue

      pads.push({
        shape: "circle",
        padId: hole.pcb_hole_id,
        layer: options.layer, // holes are through-all
        connectivityKey: `hole:${hole.pcb_hole_id}`,
        x: hole.x,
        y: hole.y,
        radius: hole.hole_diameter / 2,
      } as InputCircularPad)
    } else if (elm.type === "pcb_cutout") {
      const cutout = elm as any
      if (cutout.shape === "rect") {
        pads.push({
          shape: "rect",
          padId: cutout.pcb_cutout_id,
          layer: options.layer, // through-all
          connectivityKey: `cutout:${cutout.pcb_cutout_id}`,
          bounds: {
            minX: cutout.center.x - cutout.width / 2,
            minY: cutout.center.y - cutout.height / 2,
            maxX: cutout.center.x + cutout.width / 2,
            maxY: cutout.center.y + cutout.height / 2,
          },
        } as InputRectPad)
      } else if (cutout.shape === "circle") {
        pads.push({
          shape: "circle",
          padId: cutout.pcb_cutout_id,
          layer: options.layer, // through-all
          connectivityKey: `cutout:${cutout.pcb_cutout_id}`,
          x: cutout.center.x,
          y: cutout.center.y,
          radius: cutout.radius,
        } as InputCircularPad)
      } else if (cutout.shape === "polygon") {
        pads.push({
          shape: "polygon",
          padId: cutout.pcb_cutout_id,
          layer: options.layer, // through-all
          connectivityKey: `cutout:${cutout.pcb_cutout_id}`,
          points: cutout.points,
        } as InputPolygonPad)
      }
    } else if (elm.type === "pcb_keepout") {
      const keepout = elm as PCBKeepout
      if (!keepout.layers.includes(options.layer)) continue

      if (keepout.shape === "rect") {
        pads.push({
          shape: "rect",
          padId: keepout.pcb_keepout_id,
          layer: options.layer,
          connectivityKey: `keepout:${keepout.pcb_keepout_id}`,
          bounds: {
            minX: keepout.center.x - keepout.width / 2,
            minY: keepout.center.y - keepout.height / 2,
            maxX: keepout.center.x + keepout.width / 2,
            maxY: keepout.center.y + keepout.height / 2,
          },
        } as InputRectPad)
      } else if (keepout.shape === "circle") {
        pads.push({
          shape: "circle",
          padId: keepout.pcb_keepout_id,
          layer: options.layer,
          connectivityKey: `keepout:${keepout.pcb_keepout_id}`,
          x: keepout.center.x,
          y: keepout.center.y,
          radius: keepout.radius,
        } as InputCircularPad)
      }
    } else if (elm.type === "pcb_via") {
      const via = elm as PcbVia
      if (!via.layers.includes(options.layer)) continue

      const connectivityKey: string =
        getSubcircuitConnectivityKeyForId(via.pcb_via_id) ??
        `unconnected-via:${via.pcb_via_id}`

      pads.push({
        shape: "circle",
        padId: via.pcb_via_id,
        layer: options.layer,
        connectivityKey,
        x: via.x,
        y: via.y,
        radius: via.outer_diameter / 2,
      } as InputCircularPad)
    } else if (elm.type === "pcb_trace") {
      const trace = elm as PcbTrace
      const connectivityKey = getSubcircuitConnectivityKeyForId(
        trace.pcb_trace_id,
      )
      if (!connectivityKey) continue

      let currentSegmentGroup: Point[] = []
      let currentWidth: number | null = null

      const commitGroup = () => {
        if (currentSegmentGroup.length > 1) {
          pads.push({
            shape: "trace",
            padId: `${trace.pcb_trace_id}-${pads.length}`,
            layer: options.layer,
            connectivityKey,
            segments: currentSegmentGroup,
            width: currentWidth!,
          } as InputTracePad)
        }
        currentSegmentGroup = []
        currentWidth = null
      }

      for (const r of trace.route) {
        const ri = r as any
        const isWireOnLayer =
          ri.route_type === "wire" && ri.layer === options.layer
        if (isWireOnLayer) {
          const point = { x: ri.x, y: ri.y }

          if (currentWidth === null) {
            currentWidth = ri.width
          } else if (ri.width !== currentWidth) {
            currentSegmentGroup.push(point)
            commitGroup()
            currentWidth = ri.width
          }

          currentSegmentGroup.push(point)
        } else {
          commitGroup()
        }
      }
      commitGroup()
    }
  }

  const { width, height } = pcb_board

  // Use pour-specific outline if provided, otherwise fall back to board outline
  const outline = options.outline ?? pcb_board.outline

  let bounds: { minX: number; minY: number; maxX: number; maxY: number }
  if (outline && outline.length > 0) {
    const xs = outline.map((p) => p.x)
    const ys = outline.map((p) => p.y)
    bounds = {
      minX: Math.min(...xs),
      minY: Math.min(...ys),
      maxX: Math.max(...xs),
      maxY: Math.max(...ys),
    }
  } else {
    bounds = {
      minX: -width! / 2,
      minY: -height! / 2,
      maxX: width! / 2,
      maxY: height! / 2,
    }
  }

  const regionsForPour = [
    {
      shape: "rect" as const,
      layer: options.layer,
      bounds,
      boardEdgeOutline: options.board_edge_outline,
      outline,
      connectivityKey: pourConnectivityKey,
      padMargin: options.pad_margin,
      traceMargin: options.trace_margin,
      pourMargin:
        options.pour_margin ??
        Math.max(options.pad_margin, options.trace_margin),
      board_edge_margin: options.board_edge_margin ?? 0,
      cutout_margin: options.cutout_margin,
      use_thermal_reliefs: options.use_thermal_reliefs,
      thermal_relief_spoke_width: options.thermal_relief_spoke_width,
      thermal_relief_spoke_count: options.thermal_relief_spoke_count,
    },
  ]

  return {
    pads,
    regionsForPour,
  }
}
