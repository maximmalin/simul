import { AnyCircuitElement, CircuitJson } from 'circuit-json';

interface ComponentTemplateParams {
    pinLabels?: Record<string, string[]> | Record<string, string>;
    componentName: string;
    objUrl?: string;
    circuitJson: AnyCircuitElement[];
    supplierPartNumbers?: Record<string, string[]>;
    manufacturerPartNumber?: string;
}

declare const convertCircuitJsonToTscircuit: (circuitJson: CircuitJson, opts: Omit<ComponentTemplateParams, "circuitJson">) => string;

export { convertCircuitJsonToTscircuit };
