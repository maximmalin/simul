const unitToMm: { [key: string]: number } = {
  in: 25.4,
  inch: 25.4,
  mil: 0.0254,
  mm: 1,
  m: 1000,
  cm: 10,
  ft: 304.8,
  feet: 304.8,
}

export const mm = (n: number | string): number => {
  let unit =
    typeof n === "number" ? "mm" : n.replace(/^[^a-zA-Z]+/g, "").toLowerCase()
  if (!unit) unit = "mm"
  const val = typeof n === "number" ? n : Number.parseFloat(n.split(unit)[0])

  if (unit in unitToMm) {
    return val * unitToMm[unit]
  }
  throw new Error(`Unsupported unit: ${unit}`)
}

export const mmStr = (n: number | string): string => {
  return `${mm(n)}mm`
}

export default mm
