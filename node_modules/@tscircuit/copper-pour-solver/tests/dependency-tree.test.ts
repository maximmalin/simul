import { expect, test } from "bun:test"
import manifoldPackageJson from "../node_modules/@tscircuit/manifold-2d/package.json"
import packageJson from "../package.json"

test("manifold does not add transitive production dependencies", () => {
  expect(packageJson.dependencies["@tscircuit/manifold-2d"]).toBe("^0.0.6")
  const manifest = manifoldPackageJson as {
    dependencies?: Record<string, string>
  }
  expect(manifest.dependencies ?? {}).toEqual({})
})
