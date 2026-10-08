# spicets

`spicets` is a TypeScript-first toolkit for reading, editing, and generating
SPICE netlists. The API follows the same pattern as `kicadts`: every logical
card is modeled as a class, ordered source is preserved in the root document,
and `getString()` emits deterministic SPICE text.

## Local Setup

This repository uses [Bun](https://bun.sh) for scripts and testing.

- `bun install`
- `bun test`
- `bun run typecheck`

## Build SPICE Netlists

```ts
import { promises as fs } from "node:fs"
import {
  Capacitor,
  Resistor,
  SpiceNetlist,
  Tran,
  VoltageSource,
} from "spicets"

const netlist = new SpiceNetlist({
  title: "RC low-pass demo",
  cards: [
    new VoltageSource({
      name: "V1",
      nodes: ["vin", "0"],
      dc: 5,
    }),
    new Resistor({
      name: "R1",
      nodes: ["vin", "vout"],
      resistance: "10k",
    }),
    new Capacitor({
      name: "C1",
      nodes: ["vout", "0"],
      capacitance: "100n",
    }),
    new Tran({
      step: "1us",
      stop: "10ms",
    }),
  ],
})

await fs.writeFile("rc.cir", netlist.getString({ format: "pretty" }))
```

Emits:

```spice
RC low-pass demo
V1 vin 0 DC 5
R1 vin vout 10k
C1 vout 0 100n
.tran 1us 10ms
.end
```

## Load and Modify Existing Netlists

```ts
import { promises as fs } from "node:fs"
import { Resistor, Tran, parseSpiceNetlist } from "spicets"

const netlist = parseSpiceNetlist(await fs.readFile("amplifier.cir", "utf8"), {
  dialect: "ngspice",
})

const rload = netlist.findElement("RLOAD")

if (rload instanceof Resistor) {
  rload.resistance.raw = "2.2k"
}

netlist.add(new Tran({ step: "10n", stop: "10u" }))

await fs.writeFile("amplifier.cir", netlist.getString())
```

## Subcircuits

```ts
import { Op, Resistor, SpiceNetlist, Subckt, SubcktInstance, VoltageSource } from "spicets"

const divider = new Subckt({
  name: "divider",
  pins: ["vin", "vout", "0"],
  params: {
    rtop: "10k",
    rbot: "10k",
  },
  cards: [
    new Resistor({
      name: "Rtop",
      nodes: ["vin", "vout"],
      resistance: "{rtop}",
    }),
    new Resistor({
      name: "Rbot",
      nodes: ["vout", "0"],
      resistance: "{rbot}",
    }),
  ],
})

const netlist = new SpiceNetlist({
  title: "divider demo",
  cards: [
    divider,
    new VoltageSource({
      name: "V1",
      nodes: ["vin", "0"],
      dc: 5,
    }),
    new SubcktInstance({
      name: "X1",
      nodes: ["vin", "vout", "0"],
      subckt: "divider",
      params: {
        rtop: "20k",
      },
    }),
    new Op(),
  ],
})
```

## Source Waveforms

```ts
import { Pulse, VoltageSource } from "spicets"

const clock = new VoltageSource({
  name: "VCLK",
  nodes: ["clk", "0"],
  transient: new Pulse({
    initial: 0,
    pulsed: 3.3,
    delay: "1ns",
    rise: "100ps",
    fall: "100ps",
    width: "5ns",
    period: "10ns",
  }),
})
```

## API Shape

- `parseToSpiceTokens()` / `tokenizeSpice()` are the low-level tokenizer layer,
  equivalent in spirit to `kicadts`'s S-expression parser.
- `SpiceCard.register()` maps tokenized cards to classes through each class's
  `fromSpiceTokens()` static method, mirroring the `SxClass.register(...)`
  pattern in `kicadts`.
- `SpiceNetlist.cards` is the ordered source of truth.
- Typed getters expose `elements`, `directives`, `subckts`, `models`, and
  `analyses`.
- Every card class extends `SpiceCard`; element cards extend `ElementCard`; dot
  commands extend `DotCommand`.
- Values stay source-level by default. Strings such as `"10k"`, `"1meg"`,
  `"{rload}"`, and `"agauss(0,1,1)"` are preserved instead of evaluated.
- Unknown syntax is represented as `UnknownElementCard`, `UnknownDotCommand`, or
  `RawCard` so files can round-trip without dropping vendor-specific content.

## Parse Functions

```ts
parseToSpiceTokens(source)
parseSpiceNetlist(source)
parseSpiceLibrary(source)
parseSpiceCards(source)
parseSpiceCard(source)
```

Use `parseToSpiceTokens()` for editor tools and low-level inspection,
`parseSpiceNetlist()` for complete decks, and `parseSpiceCards()` for
card-level transforms.
