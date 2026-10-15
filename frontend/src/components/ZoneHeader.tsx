import { useEffect, useState } from 'react';
import { Radio, Activity, Radiation, AlertTriangle } from 'lucide-react';
import { WeatherBar } from '@/components/WeatherBar';
import { getZone, type ZoneTelemetry } from '@/lib/bridge';

interface ZoneHeaderProps {
  agentCount: number;
  activeProjects: number;
  totalCycles: number;
}

function useClock() {
  const [time, setTime] = useState(new Date());
  useEffect(() => {
    const interval = setInterval(() => setTime(new Date()), 1000);
    return () => clearInterval(interval);
  }, []);
  return time;
}

export function ZoneHeader({ agentCount, activeProjects, totalCycles }: ZoneHeaderProps) {
  const [zone, setZone] = useState<ZoneTelemetry | null>(null);
  // Телеметрия приходит с агентного моста вместо жёстко зашитых строк.
  useEffect(() => {
    let alive = true;
    const load = () =>
      getZone().then((z) => { if (alive) setZone(z); }).catch(() => {});
    load();
    const t = setInterval(load, 15_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const time = useClock();
  const timeStr = time.toLocaleTimeString('en-GB', { hour12: false });

  return (
    <header className="relative bg-zone-50 text-zone-900 overflow-hidden noise-overlay">
      {/* Atmospheric glow */}
      <div className="absolute inset-0 bg-zone-noise pointer-events-none" />
      <div className="absolute inset-0 bg-rust-texture pointer-events-none" />

      {/* Weather / environmental telemetry bar */}
      <WeatherBar />

      {/* Ticker bar */}
      <div className="relative border-b border-blood-800/30 bg-black/40 overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-1.5">
          <span className="status-dot-active flex-shrink-0" />
          <span className="font-mono text-[9px] tracking-[0.3em] text-blood-500 uppercase flex-shrink-0">
            Live Feed
          </span>
          <div className="flex-1 overflow-hidden">
            <div className="flex gap-10 animate-ticker whitespace-nowrap font-mono text-[9px] tracking-widest text-zone-700 uppercase">
              {Array.from({ length: 2 }).map((_, i) => (
                <span key={i} className="flex gap-10">
                  <span>Build: {zone?.status_label ?? '—'}</span>
                  <span>Tj: {zone?.radiation_display ?? '—'}</span>
                  <span>Stages: {zone?.anomalies_count ?? 0} pending</span>
                  <span>Tests: {zone?.network_display ?? '—'}</span>
                  <span>Chain: {zone?.chain_done ?? 0}/{zone?.chain_total ?? 0}</span>
                  <span>Cycles: {totalCycles.toLocaleString()}</span>
                  <span>Agents Deployed: {agentCount}</span>
                  <span>Active Projects: {activeProjects}</span>
                  <span>Classification: RESTRICTED</span>
                  <span>Next Sweep: 0600</span>
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Main header */}
      <div className="relative px-6 md:px-10 py-10 md:py-14">
        <div className="flex flex-col md:flex-row md:items-end justify-between gap-6">
          <div>
            {/* Bureau mark */}
            <div className="flex items-center gap-3 mb-5">
              <div className="relative">
                <div className="w-14 h-14 bg-blood-600 flex items-center justify-center clip-corner-tl border border-blood-400/30">
                  <Radio className="w-7 h-7 text-zone-900" strokeWidth={2} />
                </div>
                <div className="absolute -bottom-1 -right-1 w-5 h-5 bg-zone-900 flex items-center justify-center border border-blood-500/30">
                  <span className="text-[8px] font-bold text-blood-500 font-mono">SH</span>
                </div>
              </div>
              <div>
                <div className="font-mono text-[10px] tracking-[0.25em] text-blood-500 uppercase">
                  Bureau No. 7
                </div>
                <div className="font-mono text-[9px] tracking-[0.2em] text-zone-600 uppercase">
                  Division of Theoretical & Applied Engineering
                </div>
              </div>
            </div>

            {/* Title */}
            <h1 className="font-display font-700 text-4xl md:text-6xl lg:text-7xl uppercase tracking-tight leading-[0.9] text-zone-900">
              Seren <span className="text-blood-500 text-glow-blood">+</span> Hermes
            </h1>
            <p className="font-display font-300 text-lg md:text-2xl uppercase tracking-[0.15em] text-zone-700 mt-2">
              Engineering Bureau
            </p>
            <p className="font-mono text-[11px] tracking-wider text-zone-600 mt-3 max-w-lg">
              Виртуальное конструкторское бюро · Многогентная инженерия
            </p>
          </div>

          {/* Clock + stats */}
          <div className="flex flex-col items-start md:items-end gap-3">
            <div className="flex items-center gap-2 font-mono">
              <Activity className="w-4 h-4 text-toxic-400" />
              <span className="text-2xl md:text-3xl font-bold text-zone-900 tabular-nums tracking-wider">
                {timeStr}
              </span>
              <span className="text-xs text-zone-600 uppercase tracking-widest">UTC</span>
            </div>
            <div className="flex gap-2">
              <div className="bg-zone-200/50 border border-zone-400/30 px-3 py-2 clip-corner-tl">
                <div className="font-mono text-[8px] tracking-widest text-zone-600 uppercase">Agents</div>
                <div className="font-display text-xl font-bold text-zone-900">{agentCount}</div>
              </div>
              <div className="bg-zone-200/50 border border-zone-400/30 px-3 py-2 clip-corner-tl">
                <div className="font-mono text-[8px] tracking-widest text-zone-600 uppercase">Projects</div>
                <div className="font-display text-xl font-bold text-zone-900">{activeProjects}</div>
              </div>
              <div className="bg-blood-900/20 border border-blood-700/30 px-3 py-2 clip-corner-tl">
                <div className="font-mono text-[8px] tracking-widest text-blood-500 uppercase">Cycles</div>
                <div className="font-display text-xl font-bold text-blood-500">{totalCycles.toLocaleString()}</div>
              </div>
            </div>
          </div>
        </div>

        {/* Warning stripe divider */}
        <div className="mt-8 flex items-center gap-3">
          <Radiation className="w-5 h-5 text-toxic-400 animate-geiger" />
          <div className="h-1 warning-stripe flex-1 opacity-40" />
          <AlertTriangle className="w-4 h-4 text-amber-400" />
          <div className="h-1 warning-stripe w-20 opacity-40" />
          <div className="h-px bg-zone-400/30 flex-1" />
        </div>
      </div>
    </header>
  );
}
