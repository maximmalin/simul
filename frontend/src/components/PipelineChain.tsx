import { Skull, Brain, DraftingCompass, ScanLine, FlaskRound, Gauge, ChevronRight, Package, Settings2, Cpu } from 'lucide-react';
import type { Agent, Project, Task } from '@/lib/supabase';
import { ROLE_ICONS, PROJECT_STATUS_CONFIG, PRIORITY_CONFIG } from '@/lib/constants';

interface PipelineChainProps {
  agents: Agent[];
  projects: Project[];
  tasks: Task[];
}

interface PipelineStage {
  roleKey: string;
  label: string;
  subLabel: string;
  icon: typeof Skull;
}

const PIPELINE_STAGES: PipelineStage[] = [
  { roleKey: 'chief', label: 'Command', subLabel: 'Оркестрация', icon: Skull },
  { roleKey: 'theorist', label: 'Theory', subLabel: 'Исследование', icon: Brain },
  { roleKey: 'designer', label: 'Design', subLabel: 'Конструирование', icon: DraftingCompass },
  { roleKey: 'analyst', label: 'Analysis', subLabel: 'Анализ', icon: ScanLine },
  { roleKey: 'tester', label: 'Testing', subLabel: 'Испытания', icon: FlaskRound },
  { roleKey: 'optimizer', label: 'Optimize', subLabel: 'Оптимизация', icon: Gauge },
];

const PRODUCT_SPECS: Record<string, { product: string; specs: { label: string; value: string }[] }> = {
  'PROJECT-CHRYN-01': {
    product: 'Lattice Propulsion Core MK-IV',
    specs: [
      { label: 'Topology', value: 'Adaptive 7-G' },
      { label: 'Thermal Range', value: '−40 to +320°C' },
      { label: 'Lattice Density', value: '14.7 nodes/cm³' },
      { label: 'Coupling Eff.', value: '87.2%' },
    ],
  },
  'PROJECT-VEKT-02': {
    product: 'Vektor Telemetry Pipeline v2',
    specs: [
      { label: 'Throughput', value: '2.1M evt/s' },
      { label: 'Latency', value: '<47ms' },
      { label: 'Anomaly Det.', value: 'Real-time' },
      { label: 'Buffer', value: '512MB/ch' },
    ],
  },
  'PROJECT-NOVA-03': {
    product: 'Novaya Zarya Coupling Matrix',
    specs: [
      { label: 'Matrix Order', value: '12×12' },
      { label: 'Field Type', value: 'Multi-axis' },
      { label: 'Coupling Mode', value: 'Variable' },
      { label: 'Status', value: 'Theoretical' },
    ],
  },
};

export function PipelineChain({ agents, projects, tasks }: PipelineChainProps) {
  const getStageStatus = (roleKey: string, projectId: string): 'active' | 'done' | 'pending' | 'idle' => {
    const agent = agents.find((a) => a.role_key === roleKey);
    if (!agent) return 'idle';
    const projectTasks = tasks.filter((t) => t.project_id === projectId && t.agent_id === agent.id);
    if (projectTasks.length === 0) return 'pending';
    if (projectTasks.every((t) => t.status === 'done')) return 'done';
    if (projectTasks.some((t) => t.status === 'in_progress' || t.status === 'review')) return 'active';
    return 'pending';
  };

  const activeProjects = projects.filter((p) => p.status === 'active' || p.status === 'planning');

  return (
    <div className="space-y-4">
      {/* Section header */}
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 bg-toxic-600 flex items-center justify-center clip-corner-tl">
          <Settings2 className="w-4 h-4 text-zone-900" />
        </div>
        <div>
          <h2 className="font-display font-600 text-xl uppercase tracking-tight text-zone-900">
            Production Pipeline
          </h2>
          <p className="font-mono text-[10px] tracking-widest text-zone-600 uppercase">
            Технологическая Цепочка · Real-Time Process Flow
          </p>
        </div>
      </div>

      {/* Pipeline chains per project */}
      <div className="space-y-4">
        {activeProjects.map((project) => {
          const specs = PRODUCT_SPECS[project.code];
          const pStatus = PROJECT_STATUS_CONFIG[project.status];
          const pPriority = PRIORITY_CONFIG[project.priority];

          return (
            <div key={project.id} className="bg-zone-panel border border-zone-400/20 clip-corner overflow-hidden">
              {/* Project label bar */}
              <div className="flex items-center gap-3 px-5 py-3 border-b border-zone-400/15 bg-zone-200/20">
                <span className="font-mono text-[10px] tracking-widest text-blood-500 uppercase font-bold">
                  {project.code}
                </span>
                <span className={`px-2 py-0.5 text-[9px] font-mono uppercase tracking-widest rounded-sm border ${pStatus.bg} ${pStatus.color} ${pStatus.border}`}>
                  {pStatus.label}
                </span>
                <span className={`px-2 py-0.5 text-[9px] font-mono uppercase tracking-widest rounded-sm ${pPriority.bg} ${pPriority.color}`}>
                  {pPriority.label}
                </span>
                <div className="ml-auto flex items-center gap-2">
                  <span className="font-mono text-[10px] text-zone-600 uppercase tracking-wider">Progress</span>
                  <span className="font-mono text-sm font-bold text-zone-900 tabular-nums">{project.progress}%</span>
                  <div className="w-24 h-1.5 progress-track rounded-sm overflow-hidden">
                    <div className="h-full bg-blood-500" style={{ width: `${project.progress}%` }} />
                  </div>
                </div>
              </div>

              {/* Pipeline chain — horizontal on desktop, vertical on mobile */}
              <div className="p-5">
                <div className="flex flex-col lg:flex-row items-stretch lg:items-center gap-2 lg:gap-0">
                  {PIPELINE_STAGES.map((stage, idx) => {
                    const Icon = stage.icon;
                    const stageStatus = getStageStatus(stage.roleKey, project.id);
                    const agent = agents.find((a) => a.role_key === stage.roleKey);

                    return (
                      <div key={stage.roleKey} className="flex flex-col lg:flex-row items-center gap-2 lg:gap-0 flex-1">
                        {/* Stage node */}
                        <div className="flex flex-col items-center gap-1.5 w-full lg:w-auto">
                          <div
                            className={`
                              relative w-14 h-14 flex items-center justify-center clip-corner-tl border-2 transition-all
                              ${stageStatus === 'active'
                                ? 'bg-toxic-700/20 border-toxic-400 shadow-lg shadow-toxic-700/20'
                                : stageStatus === 'done'
                                ? 'bg-zone-300/20 border-zone-500/30'
                                : 'bg-zone-200/30 border-zone-400/20'
                              }
                            `}
                          >
                            <Icon
                              className={`w-6 h-6 transition-colors
                                ${stageStatus === 'active'
                                  ? 'text-toxic-400'
                                  : stageStatus === 'done'
                                  ? 'text-zone-600'
                                  : 'text-zone-600/50'
                                }`}
                              strokeWidth={stageStatus === 'active' ? 2.5 : 1.5}
                            />
                            {stageStatus === 'active' && (
                              <span className="absolute -top-1 -right-1 status-dot-active" />
                            )}
                            {stageStatus === 'done' && (
                              <span className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-zone-500" />
                            )}
                          </div>
                          {/* Labels */}
                          <div className="text-center">
                            <div className={`font-mono text-[9px] font-bold tracking-wider uppercase
                              ${stageStatus === 'active' ? 'text-toxic-400' : 'text-zone-600'}`}>
                              {stage.label}
                            </div>
                            <div className="font-mono text-[8px] tracking-widest text-zone-600/60 uppercase">
                              {stage.subLabel}
                            </div>
                            {agent && stageStatus === 'active' && (
                              <div className="font-mono text-[8px] text-blood-500/80 tracking-wider mt-0.5">
                                {agent.codename}
                              </div>
                            )}
                          </div>
                        </div>

                        {/* Connector line */}
                        {idx < PIPELINE_STAGES.length - 1 && (
                          <div className="flex lg:flex-1 items-center justify-center lg:px-1 py-1 lg:py-0">
                            <div className={`hidden lg:block h-px flex-1 ${
                              stageStatus === 'done' ? 'bg-zone-500/40' : 'bg-zone-400/20'
                            }`} />
                            <ChevronRight className={`w-4 h-4 flex-shrink-0 ${
                              stageStatus === 'done' ? 'text-zone-500/50' : 'text-zone-400/20'
                            }`} />
                            <div className={`hidden lg:block h-px flex-1 ${
                              stageStatus === 'active' ? 'bg-toxic-500/30' : 'bg-zone-400/20'
                            }`} />
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* Product specifications */}
                {specs && (
                  <div className="mt-5 pt-4 border-t border-zone-400/15">
                    <div className="flex items-center gap-2 mb-3">
                      <Package className="w-4 h-4 text-amber-400" />
                      <span className="font-mono text-[10px] tracking-widest text-zone-600 uppercase">
                        Product Specification
                      </span>
                      <div className="flex-1 h-px bg-zone-400/15" />
                    </div>
                    <div className="flex flex-col md:flex-row gap-4">
                      {/* Product name */}
                      <div className="md:w-1/3">
                        <div className="font-display text-lg font-600 uppercase tracking-tight text-zone-900">
                          {specs.product}
                        </div>
                        <div className="font-mono text-[10px] text-zone-600 mt-1">
                          {project.objective}
                        </div>
                      </div>
                      {/* Spec grid */}
                      <div className="md:flex-1 grid grid-cols-2 md:grid-cols-4 gap-2">
                        {specs.specs.map((spec) => (
                          <div key={spec.label} className="bg-zone-200/30 border border-zone-400/15 px-3 py-2 clip-corner-tl">
                            <div className="font-mono text-[8px] tracking-widest text-zone-600 uppercase">
                              {spec.label}
                            </div>
                            <div className="font-mono text-xs font-bold text-zone-900 mt-0.5">
                              {spec.value}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
