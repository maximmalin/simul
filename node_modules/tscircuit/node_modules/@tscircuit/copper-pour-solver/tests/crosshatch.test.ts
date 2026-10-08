import { expect, test } from "bun:test"
import type { BRepShape, PcbCopperPourBRep } from "circuit-json"
import { convertCircuitJsonToPcbSvg } from "circuit-to-svg"
import { CopperPourPipelineSolver, convertCircuitJsonToInputProblem } from "lib"
import type { InputPad, InputPourRegion } from "lib/types"

const region: InputPourRegion = {
  shape: "rect",
  layer: "top",
  bounds: { minX: -5, minY: -4, maxX: 5, maxY: 4 },
  connectivityKey: "net:GND",
  padMargin: 0.3,
  traceMargin: 0.2,
  board_edge_margin: 0.2,
}

const solve = (options: Partial<InputPourRegion> = {}, pads: InputPad[] = []) =>
  new CopperPourPipelineSolver({
    regionsForPour: [{ ...region, ...options }],
    pads,
  }).getOutput().brep_shapes

const ringArea = (vertices: BRepShape["outer_ring"]["vertices"]) =>
  Math.abs(
    vertices.reduce((area, p, i) => {
      const next = vertices[(i + 1) % vertices.length]!
      return area + p.x * next.y - next.x * p.y
    }, 0),
  ) / 2

const copperArea = (shapes: BRepShape[]) =>
  shapes.reduce(
    (area, shape) =>
      area +
      ringArea(shape.outer_ring.vertices) -
      shape.inner_rings.reduce(
        (holes, ring) => holes + ringArea(ring.vertices),
        0,
      ),
    0,
  )

const insideRing = (x: number, y: number, ring: BRepShape["outer_ring"]) => {
  let inside = false
  for (
    let i = 0, j = ring.vertices.length - 1;
    i < ring.vertices.length;
    j = i++
  ) {
    const a = ring.vertices[i]!
    const b = ring.vertices[j]!
    if (
      a.y > y !== b.y > y &&
      x < ((b.x - a.x) * (y - a.y)) / (b.y - a.y) + a.x
    )
      inside = !inside
  }
  return inside
}

const contains = (shapes: BRepShape[], x: number, y: number) =>
  shapes.some(
    (shape) =>
      insideRing(x, y, shape.outer_ring) &&
      !shape.inner_rings.some((ring) => insideRing(x, y, ring)),
  )

test("custom hatch sizes control opening dimensions, spacing, rim and fragment filtering", () => {
  expect(
    solve({ crosshatch: true, crosshatchPitch: 1, crosshatchWidth: 0.25 }),
  ).toEqual(solve({ crosshatch: true }))
  for (const sizing of [
    { crosshatchPitch: 2, crosshatchWidth: 0.4 },
    { crosshatchPitch: 1.5 },
    { crosshatchWidth: 0.35 },
  ]) {
    const pitch = sizing.crosshatchPitch ?? 1
    const width = sizing.crosshatchWidth ?? 0.25
    const shapes = solve({ crosshatch: true, ...sizing })
    const rings = shapes.flatMap((shape) => shape.inner_rings)
    const fullSquares = rings.filter(
      (ring) => Math.abs(ringArea(ring.vertices) - (pitch - width) ** 2) < 1e-5,
    )
    expect(fullSquares.length).toBeGreaterThan(4)
    expect(contains(shapes, 0, pitch * Math.SQRT1_2)).toBe(false)
    expect(contains(shapes, 0, 0)).toBe(true)
    for (const ring of rings) {
      expect(ringArea(ring.vertices)).toBeGreaterThanOrEqual(width ** 2 - 1e-6)
      for (const p of ring.vertices) {
        expect(Math.abs(p.x)).toBeLessThanOrEqual(5 - 0.2 - width + 1e-6)
        expect(Math.abs(p.y)).toBeLessThanOrEqual(4 - 0.2 - width + 1e-6)
      }
    }
    // At least one cell is clipped rather than omitted at the custom rim.
    expect(
      rings.some((ring) =>
        ring.vertices.some(
          (p) =>
            Math.abs(Math.abs(p.x) - (5 - 0.2 - width)) < 1e-6 ||
            Math.abs(Math.abs(p.y) - (4 - 0.2 - width)) < 1e-6,
        ),
      ),
    ).toBe(true)
    for (const ring of fullSquares) {
      const center = ring.vertices.reduce(
        (sum, p) => ({
          x: sum.x + p.x / 4,
          y: sum.y + p.y / 4,
        }),
        { x: 0, y: 0 },
      )
      for (const coordinate of [
        (center.x + center.y) * Math.SQRT1_2,
        (center.y - center.x) * Math.SQRT1_2,
      ]) {
        const cell = coordinate / pitch - 0.5
        expect(cell).toBeCloseTo(Math.round(cell), 5)
      }
    }
  }
})

test("invalid hatch sizes fail early and sizing does not enable solid pours", () => {
  for (const sizing of [
    { crosshatchPitch: 0 },
    { crosshatchPitch: -1 },
    { crosshatchPitch: Infinity },
    { crosshatchPitch: NaN },
    { crosshatchWidth: 0 },
    { crosshatchWidth: -1 },
    { crosshatchWidth: Infinity },
    { crosshatchWidth: NaN },
    { crosshatchPitch: 0.2 },
    { crosshatchWidth: 1 },
    { crosshatchPitch: 2, crosshatchWidth: 2 },
  ]) {
    expect(() => solve({ crosshatch: true, ...sizing })).toThrow(/crosshatch/)
  }
  expect(() =>
    solve({ crosshatch: true, crosshatchPitch: 1e-10, crosshatchWidth: 1e-11 }),
  ).toThrow(/candidate cells/)
  const sizing = { crosshatchPitch: 2, crosshatchWidth: 0.4 }
  expect(solve(sizing)).toEqual(solve())
  expect(solve({ ...sizing, crosshatch: false })).toEqual(solve())
})

test("crosshatch produces 45-degree holes with 1mm pitch and preserves solid-fill defaults", async () => {
  const solid = solve()
  expect(solve({ crosshatch: false })).toEqual(solid)
  const hatched = solve({ crosshatch: true })
  expect(hatched).toHaveLength(1)
  expect(hatched[0]!.outer_ring).toEqual(solid[0]!.outer_ring)
  expect(hatched[0]!.inner_rings.length).toBeGreaterThan(30)
  expect(copperArea(hatched)).toBeLessThan(copperArea(solid) * 0.7)
  // Cell centers are holes; the orthogonal diagonal grid lines stay copper.
  expect(contains(hatched, 0, Math.SQRT1_2)).toBe(false)
  expect(contains(hatched, 0, 0)).toBe(true)
  expect(contains(hatched, Math.SQRT1_2, Math.SQRT2)).toBe(false)
  const fullSquares = hatched[0]!.inner_rings.filter(
    (ring) => Math.abs(ringArea(ring.vertices) - 0.75 ** 2) < 1e-5,
  )
  expect(fullSquares.length).toBeGreaterThan(20)
  expect(fullSquares.length).toBeLessThan(hatched[0]!.inner_rings.length)
  for (const ring of fullSquares) {
    expect(ringArea(ring.vertices)).toBeCloseTo(0.75 ** 2, 5)
    const [a, b] = ring.vertices
    expect(Math.abs(a!.x - b!.x)).toBeCloseTo(Math.abs(a!.y - b!.y), 5)
  }
  const pours: PcbCopperPourBRep[] = hatched.map((brep_shape, i) => ({
    type: "pcb_copper_pour",
    pcb_copper_pour_id: `pour_${i}`,
    shape: "brep",
    layer: "top",
    covered_with_solder_mask: true,
    brep_shape,
  }))
  const svg = convertCircuitJsonToPcbSvg([
    {
      type: "pcb_board",
      pcb_board_id: "board",
      center: { x: 0, y: 0 },
      width: 10,
      height: 8,
      thickness: 1.6,
      num_layers: 2,
      material: "fr4",
    },
    ...pours,
    {
      type: "pcb_note_text",
      pcb_note_text_id: "label",
      text: "CROSSHATCH: 0.25mm / 1mm / 45deg",
      font_size: 0.35,
      font: "tscircuit2024",
      layer: "top",
      anchor_position: { x: 0, y: -4.5 },
      anchor_alignment: "center",
    },
  ])
  await expect(svg).toMatchSvgSnapshot(import.meta.path)
})

test("off-grid pads, vias and traces remain solid-connected", () => {
  const pads: InputPad[] = [
    {
      shape: "circle",
      padId: "via",
      layer: "top",
      connectivityKey: "net:GND",
      x: 0,
      y: Math.SQRT1_2,
      radius: 0.08,
    },
    {
      shape: "trace",
      padId: "trace",
      layer: "top",
      connectivityKey: "net:GND",
      width: 0.12,
      segments: [
        { x: -3, y: -1.1 },
        { x: 3, y: -1.1 },
      ],
    },
  ]
  expect(contains(solve({ crosshatch: true }), 0, Math.SQRT1_2)).toBe(false)
  const hatched = solve({ crosshatch: true }, pads)
  expect(hatched).toHaveLength(1)
  expect(contains(hatched, 0, Math.SQRT1_2)).toBe(true)
  for (let x = -3; x <= 3; x += 0.1)
    expect(contains(hatched, x, -1.1)).toBe(true)
  // Same coordinates on another layer must not suppress openings.
  expect(
    contains(
      solve({ crosshatch: true }, [{ ...pads[0]!, layer: "bottom" }]),
      0,
      Math.SQRT1_2,
    ),
  ).toBe(false)
})

test("crosshatch preserves thermal spokes and foreign-net/hole clearances", () => {
  const pads: InputPad[] = [
    {
      shape: "circle",
      padId: "thermal",
      layer: "top",
      connectivityKey: "net:GND",
      x: 0,
      y: Math.SQRT1_2,
      radius: 0.3,
      isSmtPad: true,
    },
    {
      shape: "circle",
      padId: "signal",
      layer: "top",
      connectivityKey: "net:VCC",
      x: 2,
      y: 1,
      radius: 0.4,
    },
    {
      shape: "rect",
      padId: "cutout",
      layer: "top",
      connectivityKey: "cutout:1",
      bounds: { minX: -3, minY: -2, maxX: -2, maxY: 2 },
    },
  ]
  const options = {
    use_thermal_reliefs: true,
    thermal_relief_spoke_width: 0.2,
    cutout_margin: 0.2,
  }
  const solid = solve(options, pads)
  const hatched = solve({ ...options, crosshatch: true }, pads)
  expect(hatched).toHaveLength(solid.length)
  expect(copperArea(hatched)).toBeLessThan(copperArea(solid))
  for (let d = 0; d <= 0.9; d += 0.025) {
    for (const [dx, dy] of [
      [d, 0],
      [-d, 0],
      [0, d],
      [0, -d],
    ])
      expect(contains(hatched, dx!, Math.SQRT1_2 + dy!)).toBe(true)
  }
  expect(contains(hatched, 0.35, Math.SQRT1_2 + 0.35)).toBe(false)
  expect(contains(hatched, 2.6, 1)).toBe(false)
  expect(contains(hatched, -1.9, 0)).toBe(false)
})

test("narrow necks and a tiny obstacle inside a hatch cell are preserved", () => {
  const outline = [
    { x: -4, y: -2 },
    { x: -1, y: -2 },
    { x: -1, y: -0.15 },
    { x: 1, y: -0.15 },
    { x: 1, y: -2 },
    { x: 4, y: -2 },
    { x: 4, y: 2 },
    { x: 1, y: 2 },
    { x: 1, y: 0.15 },
    { x: -1, y: 0.15 },
    { x: -1, y: 2 },
    { x: -4, y: 2 },
  ]
  const hatched = solve({ crosshatch: true, outline, board_edge_margin: 0 })
  expect(hatched).toHaveLength(1)
  expect(contains(hatched, 0, 0)).toBe(true)
  expect(hatched[0]!.inner_rings.length).toBeGreaterThan(0)
  const obstacle: InputPad = {
    shape: "circle",
    padId: "tiny",
    layer: "top",
    connectivityKey: "net:VCC",
    x: 0,
    y: Math.SQRT1_2,
    radius: 0.01,
  }
  const aroundObstacle = solve({ crosshatch: true, padMargin: 0 }, [obstacle])
  expect(contains(aroundObstacle, 0, Math.SQRT1_2 + 0.15)).toBe(true)
  expect(contains(aroundObstacle, 0, Math.SQRT1_2)).toBe(false)
})

test("higher-priority crosshatch reserves its openings from other nets", () => {
  const result = new CopperPourPipelineSolver({
    pads: [],
    regionsForPour: [
      { ...region, connectivityKey: "net:VCC" },
      { ...region, crosshatch: true },
    ],
  }).getOutput()
  expect(result.brep_shapes_by_region[0]).toHaveLength(0)
  expect(
    result.brep_shapes_by_region[1]![0]!.inner_rings.length,
  ).toBeGreaterThan(30)
})

test("converter forwards crosshatch independently for each region", () => {
  const input = convertCircuitJsonToInputProblem(
    [
      {
        type: "source_net",
        source_net_id: "gnd",
        name: "GND",
        member_source_group_ids: [],
        subcircuit_connectivity_map_key: "net:GND",
      },
      {
        type: "pcb_board",
        pcb_board_id: "board",
        center: { x: 0, y: 0 },
        width: 10,
        height: 8,
        thickness: 1.6,
        num_layers: 2,
        material: "fr4",
      },
    ],
    [
      {
        layer: "top",
        source_net_id: "gnd",
        pad_margin: 0.2,
        trace_margin: 0.2,
        crosshatch: true,
        crosshatchPitch: 2,
        crosshatchWidth: 0.4,
      },
      {
        layer: "bottom",
        source_net_id: "gnd",
        pad_margin: 0.2,
        trace_margin: 0.2,
      },
    ],
  )
  const output = new CopperPourPipelineSolver(input).getOutput()
  expect(
    output.brep_shapes_by_region[0]![0]!.inner_rings.length,
  ).toBeGreaterThan(4)
  expect(
    output.brep_shapes_by_region[0]![0]!.inner_rings.some(
      (ring) => Math.abs(ringArea(ring.vertices) - 1.6 ** 2) < 1e-5,
    ),
  ).toBe(true)
  expect(output.brep_shapes_by_region[1]![0]!.inner_rings).toHaveLength(0)
})

test("offset and neighboring regions share the board-anchored mesh", () => {
  const broad = solve({
    crosshatch: true,
    bounds: { minX: 20, maxX: 32, minY: -15, maxY: -3 },
  })
  const narrow = solve({
    crosshatch: true,
    bounds: { minX: 23, maxX: 29, minY: -12, maxY: -6 },
  })
  expect(narrow[0]!.inner_rings.length).toBeGreaterThan(10)
  const centers = (shapes: BRepShape[]) =>
    shapes.flatMap((shape) =>
      shape.inner_rings
        .filter((ring) => Math.abs(ringArea(ring.vertices) - 0.75 ** 2) < 1e-5)
        .map((ring) => {
          const center = ring.vertices.reduce(
            (sum, p) => ({
              x: sum.x + p.x / ring.vertices.length,
              y: sum.y + p.y / ring.vertices.length,
            }),
            { x: 0, y: 0 },
          )
          return `${center.x.toFixed(5)},${center.y.toFixed(5)}`
        }),
    )
  const broadCenters = new Set(centers(broad))
  expect(centers(narrow).every((center) => broadCenters.has(center))).toBe(true)
  expect(
    solve({
      crosshatch: true,
      bounds: { minX: 20, maxX: 32, minY: -15, maxY: -3 },
    }),
  ).toEqual(broad)
})

test("small or fully blocked regions do not create tiny openings", () => {
  const options = {
    bounds: { minX: 0, maxX: 0.8, minY: 0, maxY: 0.8 },
    board_edge_margin: 0,
  }
  expect(solve({ ...options, crosshatch: true })).toEqual(solve(options))
  expect(
    solve({ crosshatch: true }, [
      {
        shape: "rect",
        padId: "blocker",
        connectivityKey: "net:VCC",
        layer: "top",
        bounds: region.bounds,
      },
    ]),
  ).toHaveLength(0)
})

test("clipped edge openings retain the 0.25mm rim after board-edge clearance", () => {
  // Exercise both rectangular bounds and an explicit outline: board-edge margin
  // is applied by different code paths, but neither should reject edge cells.
  const outline = [
    { x: -5, y: -4 },
    { x: 5, y: -4 },
    { x: 5, y: 4 },
    { x: -5, y: 4 },
  ]
  for (const options of [{}, { outline }]) {
    const hatched = solve({ ...options, crosshatch: true })
    const holes = hatched[0]!.inner_rings
    expect(holes.some((ring) => ringArea(ring.vertices) < 0.5)).toBe(true)
    for (const ring of holes) {
      expect(ringArea(ring.vertices)).toBeGreaterThanOrEqual(0.0625)
      for (const vertex of ring.vertices) {
        expect(Math.abs(vertex.x)).toBeLessThanOrEqual(5 - 0.2 - 0.25 + 1e-6)
        expect(Math.abs(vertex.y)).toBeLessThanOrEqual(4 - 0.2 - 0.25 + 1e-6)
      }
    }
    // A partial opening reaches the inset edge rather than leaving a whole-cell
    // solid band. The copper rim immediately outside it remains intact.
    const rightEdgeHole = holes.find((ring) =>
      ring.vertices.some((p) => Math.abs(p.x - 4.55) < 1e-6),
    )!
    expect(rightEdgeHole).toBeDefined()
    const edgeY =
      rightEdgeHole.vertices
        .filter((p) => Math.abs(p.x - 4.55) < 1e-6)
        .reduce((sum, p) => sum + p.y, 0) / 2
    expect(contains(hatched, 4.54, edgeY)).toBe(false)
    expect(contains(hatched, 4.6, edgeY)).toBe(true)
  }
})

test("tiny and long thin clipped remnants are omitted while useful partial openings survive", () => {
  // Board rectangles are aligned with the 45-degree hatch axes. Removing the
  // 0.25mm rim leaves the requested rectangle inside one known grid opening.
  for (const [width, height, retained] of [
    [0.2, 0.2, false],
    [0.75, 0.1, false], // Area 0.075mm² passes the area threshold, but is a sliver.
    [0.75, 0.3, true],
  ] as const) {
    const outline = [
      [-0.125, -0.125],
      [0.375 + width, -0.125],
      [0.375 + width, 0.375 + height],
      [-0.125, 0.375 + height],
    ].map(([u, v]) => ({
      x: (u! - v!) * Math.SQRT1_2,
      y: (u! + v!) * Math.SQRT1_2,
    }))
    const options = { outline, board_edge_margin: 0 }
    const solid = solve(options)
    const hatched = solve({ ...options, crosshatch: true })
    expect(hatched).toHaveLength(1)
    if (retained) {
      expect(hatched[0]!.inner_rings).toHaveLength(1)
      expect(copperArea(solid) - copperArea(hatched)).toBeCloseTo(
        width * height,
        5,
      )
    } else {
      expect(hatched).toEqual(solid)
    }
  }
})

test("clipped squares follow concave and slanted edges while preserving the copper component", async () => {
  const outline = [
    { x: -5, y: -4 },
    { x: 5, y: -4 },
    { x: 5, y: 1 },
    { x: 2, y: 1 },
    { x: 2, y: 4 },
    { x: -5, y: 2 },
  ]
  const hatched = solve({ crosshatch: true, outline })
  expect(hatched).toHaveLength(1)
  expect(
    hatched[0]!.inner_rings.filter((ring) => ringArea(ring.vertices) < 0.55)
      .length,
  ).toBeGreaterThan(5)
  const svg = convertCircuitJsonToPcbSvg([
    {
      type: "pcb_board",
      pcb_board_id: "board",
      center: { x: 0, y: 0 },
      width: 10,
      height: 8,
      thickness: 1.6,
      num_layers: 2,
      material: "fr4",
      outline,
    },
    {
      type: "pcb_copper_pour",
      pcb_copper_pour_id: "pour",
      shape: "brep",
      layer: "top",
      covered_with_solder_mask: true,
      brep_shape: hatched[0]!,
    },
    {
      type: "pcb_note_text",
      pcb_note_text_id: "label",
      text: "CLIPPED EDGES / 0.25mm SOLID RIM",
      font_size: 0.3,
      font: "tscircuit2024",
      layer: "top",
      anchor_position: { x: 0, y: -4.5 },
      anchor_alignment: "center",
    },
  ])
  await expect(svg).toMatchSvgSnapshot(
    import.meta.path,
    "crosshatch-clipped-outline",
  )
})

test("a same-net pad inside a clipped boundary cell stays connected", () => {
  const options = { crosshatch: true }
  const before = solve(options)
  const edgeHole = before[0]!.inner_rings.find((ring) =>
    ring.vertices.some((p) => Math.abs(p.x - 4.55) < 1e-6),
  )!
  const center = edgeHole.vertices.reduce(
    (sum, p) => ({
      x: sum.x + p.x / edgeHole.vertices.length,
      y: sum.y + p.y / edgeHole.vertices.length,
    }),
    { x: 0, y: 0 },
  )
  expect(contains(before, center.x, center.y)).toBe(false)
  const after = solve(options, [
    {
      shape: "circle",
      padId: "edge-via",
      layer: "top",
      connectivityKey: "net:GND",
      ...center,
      radius: 0.03,
    },
  ])
  expect(after).toHaveLength(1)
  expect(contains(after, center.x, center.y)).toBe(true)
  expect(after[0]!.inner_rings.length).toBeLessThan(
    before[0]!.inner_rings.length,
  )
})

test("large boards filter thousands of clipped cells without exhausting WASM memory", () => {
  const hatched = solve({
    crosshatch: true,
    bounds: { minX: -50, maxX: 50, minY: -50, maxY: 50 },
    board_edge_margin: 0,
  })
  expect(hatched).toHaveLength(1)
  expect(hatched[0]!.inner_rings.length).toBeGreaterThan(9_000)
  expect(
    hatched[0]!.inner_rings.every((ring) => ringArea(ring.vertices) >= 0.0625),
  ).toBe(true)
})
