import {
  getManifoldModule,
  getManifoldModuleSync,
  type CrossSection as CrossSectionType,
} from "@tscircuit/manifold-2d"
import { describeScaledPolygons, type ScaledPolygons } from "./polygon-ring"

export const initializeManifoldGeometry = async () => {
  await getManifoldModule()
}

export const isManifoldGeometryInitialized = () =>
  Boolean(getManifoldModuleSync())

export const getCrossSection = () => {
  const manifold = getManifoldModuleSync()
  if (!manifold) {
    throw new Error(
      "Manifold geometry has not been initialized. Call initializeManifoldGeometry() before solving copper pours.",
    )
  }
  return manifold.CrossSection
}

export const runManifoldOperation = <T>(
  operation: string,
  polygons: ScaledPolygons,
  callback: () => T,
): T => {
  try {
    return callback()
  } catch (error) {
    const details = describeScaledPolygons(polygons)
    const message = error instanceof Error ? error.message : String(error)
    throw new Error(
      `${operation} failed: ${message}; details=${JSON.stringify(details)}`,
    )
  }
}

export type CrossSection = CrossSectionType
