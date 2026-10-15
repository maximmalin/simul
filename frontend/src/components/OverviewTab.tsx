import { Radiation, AlertTriangle, Activity, Skull, Waves, TrendingUp, Gauge, CheckCircle2, Cpu, Layers, Clock } from 'lucide-react';
import type { Agent, Project, Task } from '@/lib/supabase';
import { ROLE_ICONS, STATUS_CONFIG } from '@/lib/constants';
import { PipelineChain } from '@/components/PipelineChain';

interface OverviewTabProps {
  agents: Agent[];
  projects: Project[];
  tasks: Task[];
}

export function OverviewTab({ agents, projects, tasks }: OverviewTabProps) {
  const activeAgents = agents.filter((a) => a.status === 'active' || a.status === 'processing').length;
  const doneTasks = tasks.filter((t) => t.status === 'done').length;
  const inProgressTasks = tasks.filter((t) => t.status === 'in_progress').length;
  const pendingTasks = tasks.filter((t) => t.status === 'pending').length;
  const criticalTasks = tasks.filter((t) => t.priority === 'critical' && t.status !== 'done').length;
  const avgEfficiency = agents.length > 0
    ? Math.round(agents.reduce((sum, a) => sum + a.efficiency, 0) / agents.length)
    : 0;
  const totalCycles = agents.reduce((sum, a) => sum + a.cycles_completed, 0);
  const completionRate = tasks.length > 0 ? Math.round((doneTasks / tasks.length) * 100) : 0;

  const metrics = [
    { label: 'Active Personnel', value: activeAgents, total: agents.length, icon: Cpu, color: 'text-toxic-400' },
    { label: 'Projects Running', value: projects.filter((p) => p.status === 'active').length, total: projects.length, icon: Layers, color: 'text-amber-400' },
    { label: 'Tasks Closed', value: doneTasks, total: tasks.length, icon: CheckCircle2, color: 'text-toxic-400' },
    { label: 'In Progress', value: inProgressTasks, icon: Activity, color: 'text-amber-400' },
    { label: 'Queued', value: pendingTasks, icon: Clock, color: 'text-zone-700' },
    { label: 'Critical Open', value: criticalTasks, icon: AlertTriangle, color: 'text-blood-500' },
  ];

  return (
    <div className="space-y-6 px-4 md:px-6 py-6 max-w-7xl mx-auto">
      {/* Zone status banner */}
      <div className="relative bg-zone-panel border border-zone-400/30 clip-corner overflow-hidden noise-overlay">
        <div className="absolute inset-0 bg-zone-noise pointer-events-none" />
        <div className="relative flex flex-col md:flex-row items-stretch">
          {/* Left: radiation monitor */}
          <div className="flex items-center gap-4 px-5 py-4 border-b md:border-b-0 md:border-r border-zone-400/20 flex-1">
            <div className="flex-shrink-0">
              <Radiation className="w-10 h-10 text-toxic-400 animate-geiger" />
            </div>
            <div>
              <div className="font-mono text-[9px] tracking-widest text-zone-600 uppercase">Environmental</div>
              <div className="font-display text-lg font-600 text-zone-900 uppercase">Zone Status</div>
              <div className="flex items-center gap-2 mt-1">
                <span className="status-dot-active" />
                <span className="font-mono text-xs text-toxic-400 uppercase tracking-wider">Nominal</span>
                <span className="font-mono text-[10px] text-zone-600">· 3.4 R/h</span>
              </div>
            </div>
          </div>

          {/* Middle: anomaly count */}
          <div className="flex items-center gap-4 px-5 py-4 border-b md:border-b-0 md:border-r border-zone-400/20 flex-1">
            <div className="flex-shrink-0">
              <Waves className="w-10 h-10 text-amber-400" />
            </div>
            <div>
              <div className="font-mono text-[9px] tracking-widest text-zone-600 uppercase">Detected</div>
              <div className="font-display text-lg font-600 text-zone-900 uppercase">Anomalies</div>
              <div className="flex items-center gap-2 mt-1">
                <span className="font-mono text-xs text-amber-400">2 Active</span>
                <span className="font-mono text-[10px] text-zone-600">· Sector 7-G</span>
              </div>
            </div>
          </div>

          {/* Right: network integrity */}
          <div className="flex items-center gap-4 px-5 py-4 flex-1">
            <div className="flex-shrink-0">
              <TrendingUp className="w-10 h-10 text-toxic-400" />
            </div>
            <div>
              <div className="font-mono text-[9px] tracking-widest text-zone-600 uppercase">Uplink</div>
              <div className="font-display text-lg font-600 text-zone-900 uppercase">Network</div>
              <div className="flex items-center gap-2 mt-1">
                <span className="status-dot-active" />
                <span className="font-mono text-xs text-toxic-400">97.3%</span>
                <span className="font-mono text-[10px] text-zone-600">· Stable</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Production pipeline chain */}
      <PipelineChain agents={agents} projects={projects} tasks={tasks} />

      {/* Metrics grid */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        {metrics.map((m) => {
          const Icon = m.icon;
          return (
            <div
              key={m.label}
              className="bg-zone-panel border border-zone-400/20 p-3 clip-corner hover-zone"
            >
              <div className="flex items-center justify-between mb-2">
                <Icon className={`w-4 h-4 ${m.color}`} />
                {m.total !== undefined && (
                  <span className="font-mono text-[9px] text-zone-600 tabular-nums">/{m.total}</span>
                )}
              </div>
              <div className="font-display text-2xl font-700 text-zone-900 tabular-nums">
                {m.value}
              </div>
              <div className="font-mono text-[8px] tracking-widest text-zone-600 uppercase mt-0.5">
                {m.label}
              </div>
            </div>
          );
        })}
      </div>

      {/* Efficiency bars */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="bg-zone-panel border border-zone-400/20 p-4 clip-corner">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <Gauge className="w-4 h-4 text-blood-500" />
              <span className="font-mono text-[10px] tracking-widest text-zone-800 uppercase font-bold">
                Avg Efficiency
              </span>
            </div>
            <span className="font-display text-xl font-700 text-blood-500 tabular-nums">
              {avgEfficiency}%
            </span>
          </div>
          <div className="h-2.5 progress-track rounded-sm overflow-hidden">
            <div className="h-full bg-gradient-to-r from-blood-700 to-blood-500" style={{ width: `${avgEfficiency}%` }} />
          </div>
        </div>

        <div className="bg-zone-panel border border-zone-400/20 p-4 clip-corner">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-toxic-400" />
              <span className="font-mono text-[10px] tracking-widest text-zone-800 uppercase font-bold">
                Total Cycles
              </span>
            </div>
            <span className="font-display text-xl font-700 text-toxic-400 tabular-nums">
              {totalCycles.toLocaleString()}
            </span>
          </div>
          <div className="h-2.5 progress-track rounded-sm overflow-hidden">
            <div className="h-full bg-toxic-500 w-full" />
          </div>
        </div>

        <div className="bg-zone-panel border border-zone-400/20 p-4 clip-corner">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-toxic-400" />
              <span className="font-mono text-[10px] tracking-widest text-zone-800 uppercase font-bold">
                Completion Rate
              </span>
            </div>
            <span className="font-display text-xl font-700 text-toxic-400 tabular-nums">
              {completionRate}%
            </span>
          </div>
          <div className="h-2.5 progress-track rounded-sm overflow-hidden">
            <div className="h-full bg-toxic-500" style={{ width: `${completionRate}%` }} />
          </div>
        </div>
      </div>

      {/* Personnel quick status */}
      <div className="bg-zone-panel border border-zone-400/20 clip-corner overflow-hidden">
        <div className="px-5 py-3 border-b border-zone-400/20 flex items-center gap-2">
          <Skull className="w-4 h-4 text-blood-500" />
          <h3 className="font-display font-600 text-sm uppercase tracking-wide text-zone-900">
            Personnel Status
          </h3>
          <span className="font-mono text-[9px] tracking-widest text-zone-600 uppercase ml-auto">
            Quick View
          </span>
        </div>
        <div className="divide-y divide-zone-300/40">
          {agents.map((agent) => {
            const Icon = ROLE_ICONS[agent.role_key] ?? Cpu;
            const statusCfg = STATUS_CONFIG[agent.status];
            return (
              <div key={agent.id} className="flex items-center gap-3 px-5 py-3 hover:bg-zone-200/30 transition-colors">
                <div className="w-8 h-8 bg-zone-300/40 flex items-center justify-center clip-corner-tl">
                  <Icon className="w-4 h-4 text-zone-800" strokeWidth={2} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-mono text-xs font-bold text-zone-900 tracking-wider">
                    {agent.codename}
                  </div>
                  <div className="font-mono text-[9px] text-zone-600 tracking-wider uppercase">
                    {agent.zone_callsign} · {agent.role_title}
                  </div>
                </div>
                {agent.current_task && (
                  <div className="hidden md:block flex-1 min-w-0 text-right">
                    <span className="font-mono text-[10px] text-zone-700 truncate">
                      {agent.current_task}
                    </span>
                  </div>
                )}
                <div className="flex items-center gap-2 flex-shrink-0">
                  <span className={`status-dot ${statusCfg.dot}`} />
                  <span className={`font-mono text-[9px] tracking-widest uppercase ${statusCfg.color}`}>
                    {statusCfg.label}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
