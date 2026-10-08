import type { LayerRef, Point } from "circuit-json"

export interface ConvertCircuitJsonToInputProblemOptions {
  layer: LayerRef
  subcircuit_id?: string
  source_net_id?: string
  source_net_name?: string
  subcircuit_connectivity_map_key?: string
  /**
   * @deprecated Use subcircuit_connectivity_map_key, source_net_id, or
   * source_net_name. Generated connectivity-map ids are intentionally rejected.
   */
  pour_connectivity_key?: string
  pad_margin: number
  trace_margin: number
  pour_margin?: number
  board_edge_margin?: number
  /**
   * Physical PCB boundary used for board_edge_margin when outline is only a
   * clipping region, such as one piece of a partitioned same-net pour.
   */
  board_edge_outline?: Point[]
  cutout_margin?: number
  /** Enables thermal reliefs for same-net plated holes and SMT pads. */
  use_thermal_reliefs?: boolean
  /** Width of each thermal relief spoke. Required when enabled. */
  thermal_relief_spoke_width?: number
  /** Number of evenly spaced thermal relief spokes. Defaults to 4. */
  thermal_relief_spoke_count?: number
  outline?: Point[]
}
