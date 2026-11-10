/**
 * SimTab — вкладка «Симулятор»: результаты ко-симуляции проекта.
 *
 * Бэкенд отдаёт всё одним запросом (GET /api/sim): водопад спектра,
 * спектр виртуального приёмника, 3D-модель платы, отчёт страницы,
 * энергетику тракта, пеленгацию, тепловой расчёт и надёжность.
 *
 * Ничего не считается на клиенте — только отрисовка того, что
 * посчитал `make gnuradio link-budget direction thermal reliability`.
 */

import { useEffect, useState } from 'react';
import {
  Waves, Activity, Cpu, Play, RefreshCw, Loader2, ExternalLink,
  CheckCircle2, Circle, Radio, Thermometer, ShieldCheck,
} from 'lucide-react';
import { getSim, startBuild, watchJob, artifactUrl, type SimPayload, type SimItem } from '@/lib/bridge';

const KIND_ICON = {
  png: Waves,
  json: Activity,
  glb: Cpu,
  html: ExternalLink,
} as const;

/** Иконка по роли артефакта в наборе симуляции. */
function itemIcon(item: SimItem) {
  if (item.name.includes('waterfall') || item.name.includes('spectrum')) return Waves;
  if (item.name.includes('link_budget')) return Radio;
  if (item.name.includes('direction')) return Activity;
  if (item.name.includes('thermal')) return Thermometer;
  if (item.name.includes('reliability')) return ShieldCheck;
  if (item.name.includes('board.glb')) return Cpu;
  return KIND_ICON[item.kind] ?? Activity;
}

export function SimTab() {
  const [sim, setSim] = useState<SimPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [building, setBuilding] = useState<string | null>(null);
  const [jobLog, setJobLog] = useState<string[]>([]);

  const load = async () => {
    setLoading(true);
    try {
      setSim(await getSim());
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const run = async (target: string) => {
    if (building) return;
    setBuilding(target);
    setJobLog([]);
    try {
      const job = await startBuild(target);
      watchJob(
        job.id,
        (lines) => setJobLog((l) => [...l, ...lines].slice(-40)),
        () => { setBuilding(null); load(); },
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBuilding(null);
    }
  };

  if (loading) {
    return (
      <div className="max-w-7xl mx-auto px-4 md:px-6 py-10 flex items-center gap-3 text-zone-600">
        <Loader2 className="w-4 h-4 animate-spin" /> Загрузка результатов симуляции…
      </div>
    );
  }

  if (err && !sim) {
    return (
      <div className="max-w-3xl mx-auto p-8">
        <div className="bg-zone-100 border border-zone-300 rounded-lg p-6">
          <div className="flex items-center gap-3 text-blood-500 font-semibold mb-3">
            <Link2Icon /> Мост недоступен: {err}
          </div>
          <button onClick={load} className="flex items-center gap-2 text-sm text-blood-500 hover:underline">
            <RefreshCw className="w-4 h-4" /> Повторить
          </button>
        </div>
      </div>
    );
  }

  const m = sim?.metrics;
  const waterfall = sim?.items.find((i) => i.name === 'waterfall.png' && i.exists);

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-6 py-6 space-y-8">

      {/* ============ ШАПКА ============ */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="font-display text-xl tracking-wide text-zone-900">СИМУЛЯТОР</h2>
          <p className="font-mono text-[10px] uppercase tracking-widest text-zone-600">
            GNU Radio · NEC-2 · FEM · Octave MTBF
          </p>
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono text-[11px] text-zone-600">
            {sim?.built}/{sim?.total} готово · {sim?.progress}%
          </span>
          <button onClick={load}
            className="p-2 rounded border border-zone-300 text-zone-600 hover:text-zone-900 hover:bg-zone-50">
            <RefreshCw className="w-4 h-4" />
          </button>
          <button onClick={() => run('gnuradio')} disabled={!!building}
            className="flex items-center gap-1.5 px-3 py-2 rounded bg-blood-500 text-white text-sm hover:bg-blood-600 disabled:opacity-50">
            {building ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
            {building ? building : 'Пересчитать'}
          </button>
        </div>
      </header>

      {/* прогресс сборки симуляции */}
      <div className="h-1.5 bg-zone-200 rounded overflow-hidden">
        <div className="h-full bg-blood-500 transition-all"
          style={{ width: `${sim?.progress ?? 0}%` }} />
      </div>

      {building && jobLog.length > 0 && (
        <div className="border border-zone-300 bg-zone-900 rounded-lg p-3 max-h-40 overflow-auto">
          <div className="font-mono text-[9px] uppercase text-zone-400 mb-1">make {building}</div>
          <pre className="font-mono text-[10px] text-zone-200 whitespace-pre-wrap">
            {jobLog.join('\n')}
          </pre>
        </div>
      )}

      {/* ============ ВОДОПАД СПЕКТРА ============ */}
      {waterfall && (
        <section>
          <h3 className="font-mono text-[11px] uppercase tracking-widest text-zone-600 mb-3">
            Водопад спектра
          </h3>
          <div className="border border-zone-300 rounded-lg overflow-hidden bg-zone-900">
            <img src={waterfall.url ?? ''} alt="Водопад спектра"
              className="w-full h-auto" loading="lazy" />
            <div className="px-3 py-2 font-mono text-[10px] text-zone-500 flex flex-wrap gap-x-5 gap-y-1">
              <span>шаг {waterfall.summary?.samp_rate ? `${(Number(waterfall.summary.samp_rate) / 1e6).toFixed(1)} МГц` : '—'}</span>
              <span>SNR {m?.link_budget ? '—' : '—'}</span>
              <span>{waterfall.desc}</span>
            </div>
          </div>
        </section>
      )}

      {/* ============ МЕТРИКИ СИМУЛЯЦИИ ============ */}
      <section>
        <h3 className="font-mono text-[11px] uppercase tracking-widest text-zone-600 mb-3">
          Результаты расчётов
        </h3>
        <div className="grid sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <Card icon={Radio} title="Дальность (канал A)"
            value={`${m?.link_budget?.channels?.[0]?.range_real_km ?? '—'} км`}
            sub={`шум ${m?.link_budget?.noise_floor_dbm?.toFixed(1) ?? '—'} дБм`} />
          <Card icon={Activity} title="Пеленг"
            value={`${m?.direction?.theta_est_deg?.toFixed(0) ?? '—'}°`}
            sub={`σ ±${m?.direction?.sigma_theta_deg?.toFixed(1) ?? '—'}° · FOV ±${m?.direction?.fov_deg?.toFixed(0) ?? '—'}°`} />
          <Card icon={Thermometer} title="Тепло Tj"
            value={`${m?.thermal?.tj_c?.toFixed(1) ?? '—'} °C`}
            sub={`предел ${m?.thermal?.tj_limit_c?.toFixed(0) ?? '—'} °C`} />
          <Card icon={ShieldCheck} title="Надёжность MTBF"
            value={m?.reliability?.mtbf_h ? `${Math.round(m.reliability.mtbf_h).toLocaleString('ru-RU')} ч` : '—'}
            sub={`треб. ${m?.reliability?.req_min_h ? Math.round(m.reliability.req_min_h).toLocaleString('ru-RU') : '—'} ч`} />
        </div>

        {m?.link_budget?.channels?.length ? (
          <div className="mt-3 overflow-x-auto border border-zone-300 rounded-lg">
            <table className="w-full text-sm">
              <thead className="bg-zone-200 font-mono text-[10px] uppercase text-zone-700">
                <tr>
                  <th className="px-3 py-2 text-left">Канал</th>
                  <th className="px-3 py-2 text-right">f, МГц</th>
                  <th className="px-3 py-2 text-right">Радиовидимость, км</th>
                  <th className="px-3 py-2 text-right">С запасом, км</th>
                </tr>
              </thead>
              <tbody className="font-mono text-xs">
                {m.link_budget.channels.map((c) => (
                  <tr key={c.key} className="border-t border-zone-200">
                    <td className="px-3 py-1.5">{c.title}</td>
                    <td className="px-3 py-1.5 text-right">{c.f_mhz?.toFixed(0)}</td>
                    <td className="px-3 py-1.5 text-right">{c.range_free_km?.toFixed(0)}</td>
                    <td className="px-3 py-1.5 text-right text-blood-500">{c.range_real_km?.toFixed(0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      {/* ============ АРТЕФАКТЫ ============ */}
      <section>
        <h3 className="font-mono text-[11px] uppercase tracking-widest text-zone-600 mb-3">
          Артефакты симуляции
        </h3>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {(sim?.items ?? []).map((it) => {
            const Icon = itemIcon(it);
            return (
              <article key={it.name}
                className="border border-zone-200 bg-zone-50 rounded-lg p-4 hover-zone">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <Icon className={`w-4 h-4 shrink-0 ${it.exists ? 'text-toxic-500' : 'text-zone-400'}`} />
                    <h4 className="font-medium text-sm text-zone-900 truncate">{it.title}</h4>
                  </div>
                  {it.exists
                    ? <CheckCircle2 className="w-4 h-4 text-toxic-500 shrink-0" />
                    : <Circle className="w-4 h-4 text-zone-400 shrink-0" />}
                </div>
                <p className="text-[11px] text-zone-600 mt-1 line-clamp-2">{it.desc}</p>
                {it.summary && (
                  <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-zone-600">
                    {Object.entries(it.summary).map(([k, v]) => (
                      <span key={k}>{k}: {v === null || v === undefined ? '—' : String(v)}</span>
                    ))}
                  </div>
                )}
                <div className="mt-3 flex items-center justify-between">
                  <span className="font-mono text-[9px] text-zone-400">
                    {it.size ? `${(it.size / 1024).toFixed(0)} КБ` : '—'}
                  </span>
                  {it.exists && it.url && (
                    <a href={it.url} target="_blank" rel="noreferrer"
                      className="inline-flex items-center gap-1 text-[11px] text-blood-500 hover:underline">
                      <ExternalLink className="w-3 h-3" /> открыть
                    </a>
                  )}
                </div>
              </article>
            );
          })}
        </div>
        <p className="mt-3 font-mono text-[10px] text-zone-500">
          пересборка: <code className="bg-zone-200 px-1 rounded">{sim?.run_hint}</code>
        </p>
      </section>
    </div>
  );
}

function Card({ icon: Icon, title, value, sub }: {
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

/** Заглушка иконки для offline-состояния (ленивый импорт не нужен). */
function Link2Icon() {
  return <RefreshCw className="w-5 h-5" />;
}
