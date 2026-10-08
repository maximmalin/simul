import type React from "react"

const codeSample = `import {
  CopperPourPipelineSolver,
  convertCircuitJsonToInputProblem,
  initializeManifoldGeometry,
} from "@tscircuit/copper-pour-solver"

await initializeManifoldGeometry()

const inputProblem = convertCircuitJsonToInputProblem(circuitJson, {
  layer: "top",
  source_net_name: "GND",
  pad_margin: 0.4,
  trace_margin: 0.2,
  board_edge_margin: 0.1,
  cutout_margin: 0.2,
})

const solver = new CopperPourPipelineSolver(inputProblem)
const { brep_shapes } = solver.getOutput()`

const developmentSample = `bun install
bun run build
bun test
bun start
bun run build:site`

export default function WelcomePage() {
  return (
    <main style={styles.main}>
      <header style={styles.header}>
        <p style={styles.eyebrow}>tscircuit geometry</p>
        <h1 style={styles.title}>@tscircuit/copper-pour-solver</h1>
        <p style={styles.lede}>
          Generate PCB copper pour B-Rep polygons from Circuit JSON or from a
          direct geometry input format.
        </p>
        <nav style={styles.actions} aria-label="Project links">
          <a style={{ ...styles.button, ...styles.primaryButton }} href="/">
            Cosmos docs
          </a>
          <a
            style={styles.button}
            href="https://github.com/tscircuit/copper-pour-solver"
          >
            GitHub
          </a>
          <a
            style={styles.button}
            href="https://www.npmjs.com/package/@tscircuit/copper-pour-solver"
          >
            npm
          </a>
        </nav>
      </header>

      <section style={styles.section}>
        <h2 style={styles.heading}>Install</h2>
        <pre style={styles.pre}>
          <code>bun add @tscircuit/copper-pour-solver</code>
        </pre>
      </section>

      <section style={styles.section}>
        <h2 style={styles.heading}>Use With Circuit JSON</h2>
        <p style={styles.copy}>
          Initialize the Manifold geometry runtime once, convert Circuit JSON
          into a solver input problem by source net name, then read the
          generated B-Rep shapes.
        </p>
        <pre style={styles.pre}>
          <code>{codeSample}</code>
        </pre>
      </section>

      <section style={styles.section}>
        <h2 style={styles.heading}>What The Converter Reads</h2>
        <div style={styles.grid}>
          <div style={styles.item}>
            <strong>Board geometry</strong>
            <span>
              Board bounds, board outlines, and optional pour outlines.
            </span>
          </div>
          <div style={styles.item}>
            <strong>Obstacles</strong>
            <span>
              SMT pads, plated holes, mechanical holes, vias, traces, and
              cutouts.
            </span>
          </div>
          <div style={styles.item}>
            <strong>Connectivity</strong>
            <span>
              Source net ids, source net names, or stable
              subcircuit_connectivity_map_key values.
            </span>
          </div>
          <div style={styles.item}>
            <strong>Clearances</strong>
            <span>Pad, trace, board edge, and cutout margin controls.</span>
          </div>
        </div>
      </section>

      <section style={styles.section}>
        <h2 style={styles.heading}>Development</h2>
        <pre style={styles.pre}>
          <code>{developmentSample}</code>
        </pre>
      </section>
    </main>
  )
}

const styles: Record<string, React.CSSProperties> = {
  main: {
    width: "min(980px, calc(100% - 32px))",
    margin: "0 auto",
    padding: "48px 0 64px",
    color: "#17202a",
    fontFamily:
      'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    lineHeight: 1.55,
  },
  header: {
    borderBottom: "1px solid #d7dee4",
    paddingBottom: 28,
    marginBottom: 32,
  },
  eyebrow: {
    margin: "0 0 10px",
    color: "#0b766e",
    fontSize: 13,
    fontWeight: 700,
    textTransform: "uppercase",
    letterSpacing: 0,
  },
  title: {
    margin: "0 0 12px",
    fontSize: "clamp(2rem, 7vw, 4rem)",
    lineHeight: 1,
    letterSpacing: 0,
  },
  lede: {
    maxWidth: 760,
    margin: "0 0 16px",
    color: "#5f6f7a",
    fontSize: "1.12rem",
  },
  actions: {
    display: "flex",
    flexWrap: "wrap",
    gap: 10,
    marginTop: 22,
  },
  button: {
    display: "inline-flex",
    alignItems: "center",
    minHeight: 38,
    padding: "7px 12px",
    border: "1px solid #d7dee4",
    borderRadius: 6,
    textDecoration: "none",
    color: "#17202a",
    background: "#ffffff",
    fontWeight: 650,
  },
  primaryButton: {
    color: "#ffffff",
    background: "#0b766e",
    borderColor: "#0b766e",
  },
  section: {
    marginTop: 34,
  },
  heading: {
    margin: "0 0 12px",
    fontSize: "1.35rem",
    letterSpacing: 0,
  },
  copy: {
    maxWidth: 760,
    margin: "0 0 16px",
    color: "#34444f",
  },
  pre: {
    overflowX: "auto",
    margin: "14px 0 24px",
    padding: 16,
    border: "1px solid #d7dee4",
    borderRadius: 8,
    background: "#f7f9fb",
    color: "#10212d",
    fontFamily:
      '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
    fontSize: 14,
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
    gap: 14,
    margin: "18px 0 8px",
  },
  item: {
    display: "grid",
    gap: 4,
    border: "1px solid #d7dee4",
    borderRadius: 8,
    padding: 14,
    background: "#ffffff",
  },
}
