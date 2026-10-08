import { expect, test } from "bun:test"
import mm from "../index.ts"

test("1in", () => {
  expect(mm("1in")).toBeCloseTo(25.4)
})
test("10mm", () => {
  expect(mm("10mm")).toBeCloseTo(10)
})
test("0.54mm", () => {
  expect(mm("0.54mm")).toBeCloseTo(0.54)
})
test("0.8mm", () => {
  expect(mm("0.54mm")).toBeCloseTo(0.54)
})
test("100mil", () => {
  expect(mm("100mil")).toBeCloseTo(2.54)
})
