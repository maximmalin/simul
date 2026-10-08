import { CircuitJson, SchematicNetLabel } from 'circuit-json';
import { BpcGraph } from 'bpc-graph';

declare const convertCircuitJsonToBpc: (circuitJson: CircuitJson, opts?: {
    inferNetLabels?: boolean;
    useReadableIds?: boolean;
}) => BpcGraph;

/**
 * Generate implicit net labels for schematic ports that belong to a net but
 * are not connected via a trace. Existing labels are not duplicated.
 */
declare const generateImplicitNetLabels: (circuitJson: CircuitJson) => SchematicNetLabel[];

export { convertCircuitJsonToBpc, generateImplicitNetLabels };
