import { normalizeInput } from "./normalizeInput"
import {
  PcbTraceLinter as SrjPcbTraceLinter,
  type PcbTraceLinterParams,
} from "./PcbTraceLinter"

/** Main entry point accepts Circuit JSON as well as SRJ. */
export class PcbTraceLinter extends SrjPcbTraceLinter {
  constructor(params: PcbTraceLinterParams) {
    super({ ...params, input: normalizeInput(params.input) })
    this.inputProblem = params
  }
}
