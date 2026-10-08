import createManifoldModule, {
  type ManifoldToplevel,
} from "./vendor/manifold.js"

export * from "./vendor/manifold.js"
export { createManifoldModule }

export declare const setWasmUrl: (url: string) => void
export declare const instantiateManifold: () => Promise<ManifoldToplevel>
export declare const getManifoldModule: () => Promise<ManifoldToplevel>
export declare const getManifoldModuleSync: () => ManifoldToplevel | null
