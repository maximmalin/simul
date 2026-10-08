import { AnyCircuitElement, PCBTrace, PCBPort } from 'circuit-json';

type NodeId = string;
declare function findConnectedNetworks(connections: Array<NodeId[]>): Record<string, string[]>;

declare class ConnectivityMap {
    netMap: Record<string, string[]>;
    idToNetMap: Record<string, string>;
    constructor(netMap: Record<string, string[]>);
    addConnections(connections: string[][]): void;
    getIdsConnectedToNet(netId: string): string[];
    getNetConnectedToId(id: string): string | undefined;
    areIdsConnected(id1: string, id2: string): boolean;
    areAllIdsConnected(ids: string[]): boolean;
}

declare const getSourcePortConnectivityMapFromCircuitJson: (circuitJson: AnyCircuitElement[]) => ConnectivityMap;

declare const getFullConnectivityMapFromCircuitJson: (circuitJson: AnyCircuitElement[]) => ConnectivityMap;

/**
 * A PCB Connectivity Map is a connectivity map that has analyzed what traces and ports are actually connected on the
 * PCB.
 *
 * This is useful for determining how to route a trace on the PCB. For example, you may want to determine where the
 * nearest connected net point is to connect an unrouted pin.
 */
declare class PcbConnectivityMap {
    circuitJson: AnyCircuitElement[];
    traceIdToElm: Map<string, PCBTrace>;
    portIdToElm: Map<string, PCBPort>;
    connMap: ConnectivityMap;
    constructor(circuitJson?: AnyCircuitElement[]);
    private _buildPortMap;
    private _buildTraceMap;
    private _buildTraceConnectivityMap;
    addTrace(trace: PCBTrace): void;
    _arePcbTracesConnected(trace1: PCBTrace, trace2: PCBTrace): boolean;
    areTracesConnected(traceId1: string, traceId2: string): boolean;
    getAllTracesConnectedToTrace(traceId: string): PCBTrace[];
    getAllTracesConnectedToPort(portId: string): PCBTrace[];
}

export { ConnectivityMap, PcbConnectivityMap, findConnectedNetworks, getFullConnectivityMapFromCircuitJson, getSourcePortConnectivityMapFromCircuitJson };
