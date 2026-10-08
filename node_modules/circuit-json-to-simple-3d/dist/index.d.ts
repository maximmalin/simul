import { CircuitJson } from 'circuit-json';
import { Box } from '@tscircuit/simple-3d-svg';

type AnglePreset = "angle1" | "angle2" | "left" | "right" | "left-raised" | "right-raised";

interface BackgroundOptions {
    color?: string;
    opacity?: number;
}
interface Simple3dSvgOptions {
    camera?: {
        position: {
            x: number;
            y: number;
            z: number;
        };
        lookAt: {
            x: number;
            y: number;
            z: number;
        };
        focalLength?: number;
    };
    anglePreset?: AnglePreset;
    defaultZoomMultiplier?: number;
    background?: BackgroundOptions;
    width?: number;
    height?: number;
    showAxes?: boolean;
    showOrigin?: boolean;
    showGrid?: boolean;
    showBoundingBoxes?: boolean;
}

declare function convertCircuitJsonToSimple3dScene(circuitJson: CircuitJson, opts?: Simple3dSvgOptions): Promise<{
    boxes: Box[];
    camera: any;
}>;
declare function convertCircuitJsonToSimple3dSvg(circuitJson: CircuitJson, opts?: Simple3dSvgOptions): Promise<string>;

export { type AnglePreset, type BackgroundOptions, type Simple3dSvgOptions, convertCircuitJsonToSimple3dScene, convertCircuitJsonToSimple3dSvg };
