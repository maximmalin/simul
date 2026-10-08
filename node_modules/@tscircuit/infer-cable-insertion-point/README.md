# @tscircuit/infer-cable-insertion-point

Infer a likely cable insertion center/direction for USB connector footprints from `CircuitJson` PCB geometry.

## Install

```bash
bun add @tscircuit/infer-cable-insertion-point
```

## Usage

```ts
import { Circuit } from "tscircuit"
import { convertCircuitJsonToPcbSvg } from "circuit-to-svg"
import {
  addCableInsertNoteRect,
  guessCableInsertCenter,
} from "@tscircuit/infer-cable-insertion-point"

const circuit = new Circuit()
// circuit.add(<ImportedUsbConnector name="U1" />)

await circuit.renderUntilSettled()
const circuitJson = await circuit.getCircuitJson()

const guess = guessCableInsertCenter(circuitJson)
console.log(guess)
// { x, y, side, pcbComponentId }

const { circuitJsonWithNote } = addCableInsertNoteRect(circuitJson)
const pcbSvg = convertCircuitJsonToPcbSvg(circuitJsonWithNote)
```

`addCableInsertNoteRect` appends a `pcb_note_rect` at the inferred insertion point, with dashed stroke and label (`cable-insert:<side>`), so it is easy to inspect visually in rendered PCB SVGs.

## How it works

- Finds the primary `pcb_component` in Circuit JSON.
- Computes footprint bounds using pads, holes, and silkscreen geometry.
- Compares pad envelope to overall footprint envelope.
- Chooses the side with the largest non-pad margin (`top`, `bottom`, `left`, or `right`).
- Places the insertion center slightly outside that side.

## Visual fixtures

The screenshots below come from the generated test fixtures in `tests/usb-connectors/__snapshots__`. In each image, the **green dashed rectangle** is the inferred cable insertion center.

<table>
  <tr>
    <td><img src="tests/usb-connectors/__snapshots__/TYPE_C_31_M_12.snap.svg" width="220" /><br/><code>TYPE_C_31_M_12</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/TYPE_C_31_M_17.snap.svg" width="220" /><br/><code>TYPE_C_31_M_17</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/TYPE_C_31_M_31.snap.svg" width="220" /><br/><code>TYPE_C_31_M_31</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/TYPE_C_24P_QT_143.snap.svg" width="220" /><br/><code>TYPE_C_24P_QT_143</code></td>
  </tr>
  <tr>
    <td><img src="tests/usb-connectors/__snapshots__/TYPE_C_24P_QCHT_143.snap.svg" width="220" /><br/><code>TYPE_C_24P_QCHT_143</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/TYPE_C_24P_GTJB_040.snap.svg" width="220" /><br/><code>TYPE_C_24P_GTJB_040</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/TYPEC_304_ACP16.snap.svg" width="220" /><br/><code>TYPEC_304_ACP16</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/TYPEC_304A_ACP16G.snap.svg" width="220" /><br/><code>TYPEC_304A_ACP16G</code></td>
  </tr>
  <tr>
    <td><img src="tests/usb-connectors/__snapshots__/TYPEC_304J_BCP16.snap.svg" width="220" /><br/><code>TYPEC_304J_BCP16</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/TYPEC_304YSSW_ACP16.snap.svg" width="220" /><br/><code>TYPEC_304YSSW_ACP16</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/TYPE_C_16PIN_2MD_073_.snap.svg" width="220" /><br/><code>TYPE_C_16PIN_2MD_073_</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/TYPE_C_16PIN_L8_35.snap.svg" width="220" /><br/><code>TYPE_C_16PIN_L8_35</code></td>
  </tr>
  <tr>
    <td><img src="tests/usb-connectors/__snapshots__/TYPE_C_16P_LTH6_8.snap.svg" width="220" /><br/><code>TYPE_C_16P_LTH6_8</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/TYPE_C_16P_QTWT.snap.svg" width="220" /><br/><code>TYPE_C_16P_QTWT</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/HX_TYPE_C_16PIN.snap.svg" width="220" /><br/><code>HX_TYPE_C_16PIN</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/KH_TYPE_C_16P.snap.svg" width="220" /><br/><code>KH_TYPE_C_16P</code></td>
  </tr>
  <tr>
    <td><img src="tests/usb-connectors/__snapshots__/USB_TYPE_C_018.snap.svg" width="220" /><br/><code>USB_TYPE_C_018</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/USB_TYPE_C_019.snap.svg" width="220" /><br/><code>USB_TYPE_C_019</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/USB_TYPE_C_020.snap.svg" width="220" /><br/><code>USB_TYPE_C_020</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/USB_TYPE_C_009.snap.svg" width="220" /><br/><code>USB_TYPE_C_009</code></td>
  </tr>
  <tr>
    <td><img src="tests/usb-connectors/__snapshots__/U262_061N_4BVC10.snap.svg" width="220" /><br/><code>U262_061N_4BVC10</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/U262_061N_4BVC11.snap.svg" width="220" /><br/><code>U262_061N_4BVC11</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/U262_161N_4BVC11.snap.svg" width="220" /><br/><code>U262_161N_4BVC11</code></td>
    <td><img src="tests/usb-connectors/__snapshots__/U262_241N_4BV64.snap.svg" width="220" /><br/><code>U262_241N_4BV64</code></td>
  </tr>
</table>

For the complete fixture set, see all generated snapshots under `tests/usb-connectors/__snapshots__`.
