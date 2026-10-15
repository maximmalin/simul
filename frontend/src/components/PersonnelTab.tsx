import { useState } from 'react';
import { ChevronRight, Activity, Layers, Quote, Fingerprint, Brain, AlertTriangle } from 'lucide-react';
import type { Agent, Task } from '@/lib/supabase';
import { ROLE_ICONS, STATUS_CONFIG } from '@/lib/constants';

interface PersonnelTabProps {
  agents: Agent[];
  tasks: Task[];
  selectedAgentId: string | null;
  onSelectAgent: (id: string) => void;
}

export function PersonnelTab({ agents, tasks, selectedAgentId, onSelectAgent }: PersonnelTabProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const agentTaskCount = (agentId: string) =>
    tasks.filter((t) => t.agent_id === agentId && t.status !== 'done').length;

  return (
    <div className="px-4 md:px-6 py-6 max-w-7xl mx-auto">
      {/* Section header */}
      <div className="flex items-center gap-3 mb-5">
        <div className="w-8 h-8 bg-blood-600 flex items-center justify-center clip-corner-tl">
          <Fingerprint className="w-4 h-4 text-zone-900" />
        </div>
        <div>
          <h2 className="font-display font-600 text-xl uppercase tracking-tight text-zone-900">
            Personnel Dossiers
          </h2>
          <p className="font-mono text-[10px] tracking-widest text-zone-600 uppercase">
            Classified Registry · {agents.length} Units
          </p>
        </div>
      </div>

      {/* Agent dossiers */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {agents.map((agent, idx) => {
          const Icon = ROLE_ICONS[agent.role_key] ?? Fingerprint;
          const statusCfg = STATUS_CONFIG[agent.status];
          const taskCount = agentTaskCount(agent.id);
          const isSelected = selectedAgentId === agent.id;
          const isExpanded = expandedId === agent.id;

          return (
            <div
              key={agent.id}
              className={`relative bg-zone-panel border overflow-hidden transition-all duration-200 cursor-pointer clip-corner
                ${isSelected
                  ? 'border-blood-500/50 shadow-lg shadow-blood-900/10'
                  : 'border-zone-400/20 hover:border-zone-500/40'}`}
              onClick={() => {
                onSelectAgent(agent.id);
                setExpandedId(isExpanded ? null : agent.id);
              }}
            >
              {/* Status stripe */}
              <div className={`h-1 ${
                agent.status === 'active' ? 'bg-toxic-500' :
                agent.status === 'processing' ? 'bg-amber-500' :
                agent.status === 'idle' ? 'bg-zone-500' : 'bg-blood-600'
              }`} />

              <div className="p-5">
                {/* Header */}
                <div className="flex items-start gap-4 mb-4">
                  {/* Portrait */}
                  <div className="dossier-portrait w-16 h-20 flex-shrink-0 flex items-center justify-center clip-corner-tl">
                    <Icon className="w-7 h-7 text-zone-700 relative z-10" strokeWidth={1.5} />
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-mono text-sm font-bold text-zone-900 tracking-wider">
                        {agent.codename}
                      </span>
                      <span className={`status-dot ${statusCfg.dot}`} />
                    </div>
                    <div className="font-display text-xs uppercase tracking-wide text-blood-500/80">
                      {agent.zone_callsign}
                    </div>
                    <div className="font-mono text-[9px] tracking-widest text-zone-600 uppercase mt-0.5">
                      {agent.role_title} · {agent.designation}
                    </div>
                    {/* Efficiency bar */}
                    <div className="flex items-center gap-2 mt-2">
                      <span className="font-mono text-[9px] text-zone-600 uppercase">EFF</span>
                      <div className="w-20 h-1.5 progress-track rounded-sm overflow-hidden">
                        <div className="h-full bg-blood-500" style={{ width: `${agent.efficiency}%` }} />
                      </div>
                      <span className="font-mono text-[9px] font-bold text-zone-800 tabular-nums">
                        {agent.efficiency}%
                      </span>
                    </div>
                  </div>

                  {/* Index */}
                  <div className="font-display text-3xl font-700 text-zone-400/10 select-none">
                    {String(idx + 1).padStart(2, '0')}
                  </div>
                </div>

                {/* Quote */}
                {agent.quote && (
                  <div className="flex items-start gap-2 mb-3 bg-zone-200/30 border-l-2 border-rust-500/40 px-3 py-2">
                    <Quote className="w-3 h-3 text-rust-400 flex-shrink-0 mt-0.5" />
                    <p className="font-display text-xs italic text-zone-800 leading-snug">
                      {agent.quote}
                    </p>
                  </div>
                )}

                {/* Bio */}
                {agent.bio && (
                  <p className="text-xs text-zone-700 leading-relaxed mb-3">
                    {agent.bio}
                  </p>
                )}

                {/* Traits */}
                {agent.traits.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    {agent.traits.map((trait) => (
                      <span
                        key={trait}
                        className="font-mono text-[9px] uppercase tracking-wider px-2 py-0.5 bg-zone-300/40 border border-zone-400/20 text-zone-700 rounded-sm"
                      >
                        {trait}
                      </span>
                    ))}
                  </div>
                )}

                {/* Current task */}
                {agent.current_task && (
                  <div className="flex items-start gap-1.5 mb-3 bg-blood-900/10 border-l-2 border-blood-500/40 px-2.5 py-1.5">
                    <Activity className="w-3 h-3 text-blood-500 mt-0.5 flex-shrink-0" />
                    <p className="text-[11px] text-zone-800 leading-snug font-mono">
                      {agent.current_task}
                    </p>
                  </div>
                )}

                {/* Quick stats */}
                <div className="flex items-center justify-between pt-3 border-t border-zone-400/15">
                  <div className="flex items-center gap-3">
                    <div className="flex items-center gap-1">
                      <Layers className="w-3 h-3 text-zone-600" />
                      <span className="font-mono text-[10px] text-zone-600">
                        {taskCount} active
                      </span>
                    </div>
                    <span className="font-mono text-[10px] text-zone-600">
                      {agent.cycles_completed.toLocaleString()} cycles
                    </span>
                  </div>
                  <ChevronRight
                    className={`w-4 h-4 text-zone-600 transition-transform duration-200
                      ${isExpanded ? 'rotate-90' : ''}`}
                  />
                </div>

                {/* Expanded: psych profile */}
                {isExpanded && (
                  <div className="mt-3 pt-3 border-t border-zone-400/15 animate-slide-up space-y-3">
                    {agent.psych_profile && (
                      <div className="flex items-start gap-2">
                        <Brain className="w-3.5 h-3.5 text-amber-400 flex-shrink-0 mt-0.5" />
                        <div>
                          <div className="font-mono text-[9px] tracking-widest text-amber-400 uppercase mb-1">
                            Psych Profile
                          </div>
                          <p className="text-[11px] text-zone-700 leading-relaxed">
                            {agent.psych_profile}
                          </p>
                        </div>
                      </div>
                    )}
                    <div className="flex items-start gap-2">
                      <AlertTriangle className="w-3.5 h-3.5 text-rust-400 flex-shrink-0 mt-0.5" />
                      <div>
                        <div className="font-mono text-[9px] tracking-widest text-rust-400 uppercase mb-1">
                          Specialty
                        </div>
                        <p className="text-[11px] text-zone-700 leading-relaxed">
                          {agent.specialty}
                        </p>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="bg-zone-200/30 px-2 py-1.5 border border-zone-400/15">
                        <div className="font-mono text-[8px] tracking-widest text-zone-600 uppercase">Unit ID</div>
                        <div className="font-mono text-[10px] text-zone-800">{agent.id.slice(0, 8).toUpperCase()}</div>
                      </div>
                      <div className="bg-zone-200/30 px-2 py-1.5 border border-zone-400/15">
                        <div className="font-mono text-[8px] tracking-widest text-zone-600 uppercase">Role Key</div>
                        <div className="font-mono text-[10px] text-zone-800 uppercase">{agent.role_key}</div>
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
