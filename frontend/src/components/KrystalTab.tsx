/**
 * KrystalTab — панель управления проектом Krystal Radio.
 *
 * Три блока, которых нет в остальных вкладках:
 *   1. Создание нового проекта (POST /api/projects).
 *   2. Статус проектов: прогресс, метрики приёмника, артефакты.
 *   3. Живая цепочка сборки (GET /api/chain) — 12 стадий `make all`,
 *      каждая со статусом done/stale/running/missing и кнопкой запуска
 *      от имени конкретного агента.
 *
 * Данные идут через агентный мост (backend/agent_bridge.py); если мост
 * недоступен — показывается честное сообщение, а не мок.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Radio, Plus, X, Play, RefreshCw, FolderKanban, Activity, Cpu,
  CheckCircle2, AlertTriangle, Loader2, Circle, Terminal, Link2, Zap,
} from 'lucide-react';
import {
  getChain, getProjects, createProject, runAsAgent, watchJob, getMetrics,
  artifactUrl,
  type Chain, type ChainStage, type KrProject, type Metrics, type Job,
} from '@/lib/bridge';

const STATE_STYLE: Record<string, { icon: typeof CheckCircle2; cls: string; label: string }> = {
  done:    { icon: CheckCircle2,  cls: 'text-emerald-500', label: 'готово' },
  running: { icon: Loader2,       cls: 'text-sky-400 animate-spin', label: 'идёт' },
  stale:   { icon: AlertTriangle, cls: 'text-amber-500', label: 'устарело' },
  missing: { icon: Circle,        cls: 'text-zone-500',   label: 'нет' },
};

export function KrystalTab() {
  const [chain, setChain] = useState<Chain | null>(null);
  const [projects, setProjects] = useState<KrProject[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const [showNew, setShowNew] = useState(false);
  const [code, setCode] = useState('');
  const [title, setTitle] = useState('');
  const [objective, setObjective] = useState('');
  const [saving, setSaving] = useState(false);
  const [formErr, setFormErr] = useState<string | null>(null);

  const [selected, setSelected] = useState<string | null>(null);
  const [logs, setLogs] = useState<Record<string, string[]>>({});
  const [running, setRunning] = useState<Record<string, string>>({});
  const unwatch = useRef<(() => void) | null>(null);

  const load = useCallback(async () => {
    try {
      const [c, p, m] = await Promise.all([getChain(), getProjects(), getMetrics()]);
      setChain(c);
      setProjects(p.projects);
      setMetrics(m);
      setOnline(true);
      setErr(null);
    } catch (e) {
      setOnline(false);
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => {
      clearInterval(t);
      unwatch.current?.();
    };
  }, [load]);

  const submit = async () => {
    if (!code.trim() || !title.trim()) {
      setFormErr('Нужны код и название');
      return;
    }
    setSaving(true);
    setFormErr(null);
    try {
      const r = await createProject({ code: code.trim(), title: title.trim(), objective: objective.trim() });
      if (r.error) setFormErr(r.error);
      else {
        setShowNew(false);
        setCode(''); setTitle(''); setObjective('');
        await load();
      }
    } catch (e) {
      setFormErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const run = async (stage: ChainStage, agentId: string) => {
    if (running[stage.key]) return;
    try {
      const job = await runAsAgent(agentId, stage.target);
      setRunning((r) => ({ ...r, [stage.key]: job.id }));
      setLogs((l) => ({ ...l, [stage.key]: [] }));
      unwatch.current?.();
      unwatch.current = watchJob(
        job.id,
        (lines) => setLogs((l) => ({ ...l, [stage.key]: [...(l[stage.key] ?? []), ...lines] })),
        () => {
          setRunning((r) => {
            const n = { ...r };
            delete n[stage.key];
            return n;
          });
          load();
        },
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  // ---- offline --------------------------------------------------------
  if (online === false) {
    return (
      <div className="max-w-3xl mx-auto p-8">
        <div className="bg-zone-100 border border-zone-300 rounded-lg p-6">
          <div className="flex items-center gap-3 text-blood-500 font-semibold mb-3">
            <Link2 className="w-5 h-5" /> Агентный мост недоступен
          </div>
          <p className="text-sm text-zone-700 mb-3">
            Вкладка работает через локальный HTTP-мост. Запустите его в корне проекта:
          </p>
          <pre className="bg-zone-900 text-zone-100 p-3 rounded text-xs overflow-auto">
python3 backend/agent_bridge.py --port 8787{'\n'}# затем:  make agent-api
          </pre>
          {err && <p className="text-xs text-zone-600 mt-3 font-mono">{err}</p>}
          <button onClick={load} className="mt-4 flex items-center gap-2 text-sm text-blood-500 hover:underline">
            <RefreshCw className="w-4 h-4" /> Повторить
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-6 py-6 space-y-8">

      {/* ============ ШАПКА ============ */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="font-display text-xl tracking-wide text-zone-900">KRYSTAL RADIO</h2>
          <p className="font-mono text-[10px] uppercase tracking-widest text-zone-600">
            zero-IF SDR · управление проектом агентами
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 text-[11px] font-mono px-2.5 py-1 rounded
            ${online ? 'bg-emerald-50 text-emerald-700' : 'bg-zone-100 text-zone-600'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${online ? 'bg-emerald-500' : 'bg-zone-400'}`} />
            {online ? 'BRIDGE ONLINE' : 'CONNECTING'}
          </span>
          <button onClick={load} title="Обновить"
            className="p-2 rounded border border-zone-300 text-zone-600 hover:text-zone-900 hover:bg-zone-50">
            <RefreshCw className="w-4 h-4" />
          </button>
          <button onClick={() => setShowNew(true)}
            className="flex items-center gap-1.5 px-3 py-2 rounded bg-blood-500 text-white text-sm hover:bg-blood-600">
            <Plus className="w-4 h-4" /> Новый проект
          </button>
        </div>
      </header>

      {/* ============ 1. НОВЫЙ ПРОЕКТ ============ */}
      {showNew && (
        <div className="fixed inset-0 bg-zone-900/50 flex items-center justify-center z-50 p-4">
          <div className="bg-zone-50 border border-zone-300 rounded-lg w-full max-w-lg p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="font-display text-lg text-zone-900 flex items-center gap-2">
                <FolderKanban className="w-5 h-5 text-blood-500" /> Новый проект
              </h3>
              <button onClick={() => setShowNew(false)} className="text-zone-500 hover:text-zone-900">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <label className="col-span-1 block">
                <span className="font-mono text-[10px] uppercase text-zone-600">Код</span>
                <input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())}
                  placeholder="KR-02" maxLength={24}
                  className="mt-1 w-full px-2 py-1.5 bg-white border border-zone-300 rounded text-sm font-mono" />
              </label>
              <label className="col-span-2 block">
                <span className="font-mono text-[10px] uppercase text-zone-600">Название</span>
                <input value={title} onChange={(e) => setTitle(e.target.value)}
                  placeholder="Модуль пеленгации 2.0"
                  className="mt-1 w-full px-2 py-1.5 bg-white border border-zone-300 rounded text-sm" />
              </label>
            </div>
            <label className="block">
              <span className="font-mono text-[10px] uppercase text-zone-600">Цель</span>
              <textarea value={objective} onChange={(e) => setObjective(e.target.value)} rows={3}
                placeholder="Что должен дать проект"
                className="mt-1 w-full px-2 py-1.5 bg-white border border-zone-300 rounded text-sm" />
            </label>
            {formErr && <p className="text-xs text-blood-600">{formErr}</p>}
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowNew(false)}
                className="px-3 py-2 text-sm text-zone-600 hover:text-zone-900">Отмена</button>
              <button onClick={submit} disabled={saving}
                className="px-4 py-2 text-sm bg-blood-500 text-white rounded hover:bg-blood-600 disabled:opacity-50">
                {saving ? 'Создаю…' : 'Создать'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ============ 2. ПРОЕКТЫ ============ */}
      <section>
        <h3 className="font-mono text-[11px] uppercase tracking-widest text-zone-600 mb-3">
          Проекты ({projects.length})
        </h3>
        {projects.length === 0 ? (
          <p className="text-sm text-zone-500 italic">Проектов нет — создайте первый.</p>
        ) : (
          <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {projects.map((p) => (
              <article key={p.id}
                className={`border rounded-lg p-4 cursor-pointer transition-colors
                  ${selected === p.id ? 'border-blood-500 bg-blood-500/5' : 'border-zone-300 bg-zone-100 hover:border-zone-400'}`}
                onClick={() => setSelected(selected === p.id ? null : p.id)}>
                <div className="flex items-start justify-between gap-2">
                  <span className="font-mono text-[11px] text-blood-500">{p.code}</span>
                  <span className={`font-mono text-[9px] uppercase px-1.5 py-0.5 rounded
                    ${p.status === 'completed' ? 'bg-emerald-100 text-emerald-700'
                      : p.status === 'active' ? 'bg-sky-100 text-sky-700'
                      : 'bg-zone-200 text-zone-600'}`}>{p.status}</span>
                </div>
                <h4 className="mt-1 font-medium text-zone-900 text-sm leading-snug">{p.title}</h4>
                <p className="mt-1 text-xs text-zone-600 line-clamp-2">{p.objective}</p>
                <div className="mt-3">
                  <div className="flex justify-between font-mono text-[10px] text-zone-600 mb-1">
                    <span>{p.tasks_done ?? 0}/{p.tasks_total ?? 0} задач</span>
                    <span>{p.progress}%</span>
                  </div>
                  <div className="h-1.5 bg-zone-200 rounded overflow-hidden">
                    <div className="h-full bg-blood-500 transition-all"
                      style={{ width: `${p.progress}%` }} />
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {/* ============ 3. ЦЕПОЧКА СБОРКИ ============ */}
      <section>
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="font-mono text-[11px] uppercase tracking-widest text-zone-600">
            Цепочка сборки
          </h3>
          {chain && (
            <span className="font-mono text-[11px] text-zone-600">
              <span className="text-emerald-600 font-semibold">{chain.done}</span>/{chain.total}
              {' '}стадий · {chain.progress}%
            </span>
          )}
        </div>

        {!chain ? (
          <p className="text-sm text-zone-500 italic">Загрузка…</p>
        ) : (
          <ol className="space-y-1.5">
            {chain.stages.map((s) => {
              const st = STATE_STYLE[s.state] ?? STATE_STYLE.missing;
              const Icon = st.icon;
              const jobId = running[s.key];
              const log = logs[s.key] ?? [];
              return (
                <li key={s.key}
                  className={`border rounded-lg overflow-hidden
                    ${s.state === 'running' ? 'border-sky-400 bg-sky-50/40'
                      : s.state === 'done' ? 'border-zone-300 bg-zone-100'
                      : 'border-dashed border-zone-300'}`}>
                  <div className="flex items-center gap-3 px-4 py-2.5">
                    <span className="font-mono text-[10px] text-zone-500 w-6 text-right">
                      {String(s.n).padStart(2, '0')}
                    </span>
                    <Icon className={`w-4 h-4 shrink-0 ${st.cls}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm text-zone-900">{s.label}</span>
                        <code className="font-mono text-[10px] text-zone-500 bg-zone-200 px-1 rounded">
                          {s.target}
                        </code>
                      </div>
                      <p className="text-[11px] text-zone-600 truncate">{s.sub}</p>
                    </div>

                    {s.files.length > 0 && (
                      <span className="font-mono text-[10px] text-zone-500 hidden sm:inline">
                        {s.files.length} файл(ов)
                      </span>
                    )}
                    <span className={`font-mono text-[10px] ${st.cls}`}>{st.label}</span>

                    <button
                      onClick={() => run(s, 'AGENT-MK-01')}
                      disabled={!!jobId}
                      className="flex items-center gap-1 px-2 py-1 text-[11px] rounded border
                        border-zone-300 text-zone-700 hover:bg-zone-200 disabled:opacity-40">
                      {jobId ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
                      {jobId ? 'идёт' : 'собрать'}
                    </button>
                  </div>

                  {log.length > 0 && (
                    <div className="border-t border-zone-300 bg-zone-900 px-4 py-2 max-h-40 overflow-auto">
                      <div className="flex items-center gap-1.5 font-mono text-[9px] uppercase text-zone-400 mb-1">
                        <Terminal className="w-3 h-3" /> {s.target}
                      </div>
                      <pre className="font-mono text-[10px] leading-relaxed text-zone-200 whitespace-pre-wrap">
                        {log.slice(-25).join('\n')}
                      </pre>
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        )}
      </section>

      {/* ============ 4. МЕТРИКИ ПРИЁМНИКА ============ */}
      {metrics && (
        <section>
          <h3 className="font-mono text-[11px] uppercase tracking-widest text-zone-600 mb-3">
            Метрики приёмника
          </h3>
          <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
            <MetricCard icon={Radio} title="Дальность (канал A)"
              value={`${metrics.link_budget.channels[0]?.range_real_km ?? '—'} км`}
              sub={`свободн. ${metrics.link_budget.channels[0]?.range_free_km ?? '—'} км`} />
            <MetricCard icon={Zap} title="Шум / MDS"
              value={`${metrics.link_budget.noise_floor_dbm?.toFixed(1) ?? '—'} / ${metrics.link_budget.mds_dbm?.toFixed(1) ?? '—'}`}
              sub="дБм · NF 3.5 дБ" />
            <MetricCard icon={Activity} title="Пеленг"
              value={`${metrics.direction.theta_est_deg?.toFixed(0) ?? '—'}°`}
              sub={`±${metrics.direction.sigma_theta_deg?.toFixed(1) ?? '—'}° · FOV ±${metrics.direction.fov_deg?.toFixed(0) ?? '—'}°`} />
            <MetricCard icon={Cpu} title="Тепло Tj / MTBF"
              value={`${metrics.thermal.tj_max_c?.toFixed(1) ?? '—'} °C`}
              sub={`MTBF ${metrics.reliability.mtbf_h ? `${Math.round(metrics.reliability.mtbf_h).toLocaleString('ru-RU')} ч` : '—'}`} />
          </div>

          {metrics.link_budget.channels.length > 0 && (
            <div className="mt-3 overflow-x-auto border border-zone-300 rounded-lg">
              <table className="w-full text-sm">
                <thead className="bg-zone-200 font-mono text-[10px] uppercase text-zone-700">
                  <tr>
                    <th className="px-3 py-2 text-left">Канал</th>
                    <th className="px-3 py-2 text-right">f, МГц</th>
                    <th className="px-3 py-2 text-right">G<sub>rx</sub>, дБи</th>
                    <th className="px-3 py-2 text-right">Радиовидимость, км</th>
                    <th className="px-3 py-2 text-right">С запасом 20 дБ, км</th>
                  </tr>
                </thead>
                <tbody className="font-mono text-xs">
                  {metrics.link_budget.channels.map((c) => (
                    <tr key={c.key} className="border-t border-zone-200">
                      <td className="px-3 py-1.5">{c.title}</td>
                      <td className="px-3 py-1.5 text-right">{c.f_mhz?.toFixed(0)}</td>
                      <td className="px-3 py-1.5 text-right">{c.g_rx_dbi?.toFixed(1)}</td>
                      <td className="px-3 py-1.5 text-right">{c.range_free_km?.toFixed(0)}</td>
                      <td className="px-3 py-1.5 text-right text-blood-500">{c.range_real_km?.toFixed(0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function MetricCard({ icon: Icon, title, value, sub }: {
  icon: typeof Radio; title: string; value: string; sub: string;
}) {
  return (
    <div className="border border-zone-300 bg-zone-100 rounded-lg p-4">
      <div className="flex items-center gap-2 font-mono text-[10px] uppercase text-zone-600">
        <Icon className="w-3.5 h-3.5" /> {title}
      </div>
      <div className="mt-2 font-display text-2xl text-zone-900">{value}</div>
      <div className="text-[11px] text-zone-600 mt-0.5">{sub}</div>
    </div>
  );
}