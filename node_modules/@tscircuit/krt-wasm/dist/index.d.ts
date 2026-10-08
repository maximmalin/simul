import type { AutorouterCompleteEvent, AutorouterErrorEvent, AutorouterProgressEvent, GenericLocalAutorouter, SimpleRouteJson, SimplifiedPcbTrace } from "tscircuit";
type Handler<T> = (event: T) => void;
export interface KiCadRoutingToolsAutorouterOptions {
    gridStep?: number;
    clearance?: number;
    maxIterations?: number;
    viaCost?: number;
    hWeight?: number;
    turnCost?: number;
    trackMargin?: number;
    layerCosts?: number[];
    layerDirectionPreferences?: number[];
    directionPreferenceCost?: number;
    busAttractionRadius?: number;
    busAttractionBonus?: number;
    /** @deprecated Unsafe tunnel collapsing is disabled; retained for source compatibility. */
    collapseShortSameLayerTunnels?: boolean;
}
export declare function initKiCadRoutingToolsAutorouter(): Promise<void>;
export declare class KiCadRoutingToolsAutorouter implements GenericLocalAutorouter {
    readonly input: SimpleRouteJson;
    private readonly options;
    isRouting: boolean;
    private cachedTraces;
    private completeHandlers;
    private errorHandlers;
    private progressHandlers;
    constructor(input: SimpleRouteJson, options?: KiCadRoutingToolsAutorouterOptions);
    on(event: "complete", callback: Handler<AutorouterCompleteEvent>): void;
    on(event: "error", callback: Handler<AutorouterErrorEvent>): void;
    on(event: "progress", callback: Handler<AutorouterProgressEvent>): void;
    start(): void;
    stop(): void;
    solve(): Promise<SimplifiedPcbTrace[]>;
    solveSync(): SimplifiedPcbTrace[];
    private emitComplete;
    private emitError;
    private emitProgress;
}
export declare function createKiCadRoutingToolsAutorouter(options?: KiCadRoutingToolsAutorouterOptions): (simpleRouteJson: SimpleRouteJson) => Promise<KiCadRoutingToolsAutorouter>;
export {};
