import {
  BasePipelineSolver,
  type BaseSolver,
  definePipelineStep,
  type PipelineStep,
} from "@tscircuit/solver-utils"
import { validateSimpleRouteJson } from "./validateSimpleRouteJson"
import { OddAngleFinder } from "./OddAngleFinder"
import type {
  LinterInput,
  LinterOptions,
  SimpleRouteJson,
  TraceIssue,
} from "./types"
import { visualizeIssues } from "./visualizeIssues"

export interface IssueIdentifier extends BaseSolver {
  getOutput(): TraceIssue[]
}

export interface PcbTraceLinterParams extends LinterOptions {
  input: LinterInput
}
export class PcbTraceLinter extends BasePipelineSolver<PcbTraceLinterParams> {
  srj: SimpleRouteJson
  oddAngleFinder?: OddAngleFinder
  pipelineDef: PipelineStep<IssueIdentifier>[] = [
    definePipelineStep(
      "oddAngleFinder",
      OddAngleFinder,
      (p: PcbTraceLinter) => [
        {
          srj: p.srj,
          angleToleranceDegrees: p.inputProblem.angleToleranceDegrees,
          minSegmentLength: p.inputProblem.minSegmentLength,
        },
      ],
    ),
  ]
  constructor(params: PcbTraceLinterParams) {
    super(params)
    this.srj = validateSimpleRouteJson(params.input)
    this.MAX_ITERATIONS = (this.srj.traces ?? []).reduce(
      (n, t) => n + t.route.length,
      10,
    )
  }
  /** Aggregate stages in pipeline order; future identifiers only need getOutput(): TraceIssue[]. */
  override getOutput(): TraceIssue[] {
    return this.pipelineDef.flatMap(
      (stage) =>
        this.getSolver<IssueIdentifier>(stage.solverName)?.getOutput() ?? [],
    )
  }
  override getConstructorParams(): [PcbTraceLinterParams] {
    return [this.inputProblem]
  }
  override initialVisualize() {
    return visualizeIssues(this.srj, [])
  }
  override finalVisualize() {
    return visualizeIssues(this.srj, this.getOutput())
  }
}
