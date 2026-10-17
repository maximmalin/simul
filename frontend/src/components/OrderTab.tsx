/**
 * OrderTab — приём заказа и его агентно-функциональная обработка.
 *
 * Жизненный цикл заказа здесь такой же, как задумано в движке:
 *
 *   1. оператор пишет заказ;
 *   2. движок **разбирает** его в цели сборки (`POST /api/order/plan`)
 *      и показывает план — ничего ещё не выполнено;
 *   3. оператор подтверждает, и только тогда заказ стартует;
 *   4. прогресс идёт потоком (`GET /api/order/<id>/stream`), а не опросом:
 *      журнал и смена статуса шагов приходят сразу;
 *   5. если граф встал и ждёт человека (`needs_human`), появляется
 *      вопрос, и ответ продолжает работу с того же места (`resume`).
 *
 * Пункт 2 — не украшение. Планировщик может ошибиться, и запускать
 * сборку по неверному плану дороже, чем спросить заранее.
 *
 * Данные: агентный мост (backend/agents/), не Supabase.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Cpu, CheckCircle2, XCircle, Loader2, Circle, Clock, Sparkles,
  ListTree, SkipForward, HelpCircle, Play, ScrollText, Ban, Download, Package,
} from 'lucide-react';
import { OrderConsole } from './OrderConsole';

const BASE = (import.meta.env.VITE_BRIDGE_URL as string | undefined) ?? '';

type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

interface Step {
  id: string;
  title: string;
  agent_id: string;
  kind: 'make' | 'script';
  target: string;
  status: StepStatus;
  exit_code: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  job_id: string | null;
  duration_s?: number;
  log_tail?: string[];
}

interface ArtifactItem {
  name: string;
  path: string;
  size_h: string;
  ext: string;
  mime: string;
  url: string;
  modified: string;
}

interface Order {
  id: string;
  text: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'partial' | 'cancelled';
  intent: string;
  planner: string;
  progress: number;
  verdict: string;
  question: string;
  note: string;
  engine: string;
  created_at: string;
  finished_at: string | null;
  steps: Step[];
  report: string;
  logs: string[];
}

interface Plan {
  intent: string;
  targets: string[];
  planner: string;
  note: string;
  available?: string[];
}

interface Capabilities {
  planner: string;
  engine: string;
  agents: { name: string; title: string; kind: string }[];
  runners: string[];
  verify: string;
  suggestions: string[];
}

const STATUS_ICON: Record<StepStatus, typeof Circle> = {
  pending: Circle,
  running: Loader2,
  done: CheckCircle2,
  failed: XCircle,
  skipped: SkipForward,
};

const STATUS_COLOR: Record<StepStatus, string> = {
  pending: 'text-zone-500',
  running: 'text-sky-400',
  done: 'text-emerald-500',
  failed: 'text-blood-500',
  skipped: 'text-amber-500',
};

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

const TERMINAL = ['done', 'failed', 'partial', 'cancelled'];

export function OrderTab() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [text, setText] = useState('');
  const [plan, setPlan] = useState<Plan | null>(null);
  const [order, setOrder] = useState<Order | null>(null);
  const [history, setHistory] = useState<Order[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [answer, setAnswer] = useState('');
  const [showLog, setShowLog] = useState(false);
  const [streamTick, setStreamTick] = useState(0);
  const [artifacts, setArtifacts] = useState<ArtifactItem[] | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    api<Capabilities>('/api/order/capabilities')
      .then(setCaps)
      .catch(() => setErr('движок недоступен — запустите make run'));
    api<{ orders: Order[] }>('/api/orders?limit=10')
      .then((d) => setHistory(d.orders as unknown as Order[]))
      .catch(() => {});
  }, []);

  const refreshHistory = useCallback(() => {
    api<{ orders: Order[] }>('/api/orders?limit=10')
      .then((d) => setHistory(d.orders as unknown as Order[]))
      .catch(() => {});
  }, []);

  // ---- поток событий вместо опроса ------------------------------------
  useEffect(() => {
    if (!order || order.status !== 'done') { setArtifacts(null); return; }
    api<{ count: number; artifacts: ArtifactItem[] }>('/api/artifacts')
      .then((d) => setArtifacts(d.artifacts))
      .catch(() => setArtifacts(null));
  }, [order?.id, order?.status]);

  useEffect(() => {
    if (!order) return;
    if (TERMINAL.includes(order.status)) return;
    if (order.verdict === 'needs_human') return;   // ждём ответа оператора

    const es = new EventSource(`${BASE}/api/order/${order.id}/stream`);
    esRef.current = es;

    es.addEventListener('state', (ev) => {
      setOrder(JSON.parse((ev as MessageEvent).data));
    });
    es.addEventListener('step', (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      setOrder((prev) =>
        prev
          ? {
              ...prev,
              steps: prev.steps.map((s) => {
                const upd = d.steps?.find((x: Step) => x.id === s.id);
                return upd ? { ...s, ...upd } : s;
              }),
              progress: d.progress ?? prev.progress,
            }
          : prev,
      );
    });
    es.addEventListener('log', (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      setOrder((prev) => (prev ? { ...prev, logs: [...(prev.logs ?? []), d.text] } : prev));
    });
    es.addEventListener('done', (ev) => {
      setOrder(JSON.parse((ev as MessageEvent).data));
      es.close();
      refreshHistory();
    });
    // Обрыв сети больше не убивает консоль: переподключаемся с паузой,
    // иначе живой журнал заказа замирал навсегда.
    let retry: ReturnType<typeof setTimeout> | null = null;
    let closed = false;
    es.onerror = () => {
      es.close();
      if (closed) return;
      retry = setTimeout(() => {
        if (!closed) setStreamTick((n) => n + 1);
      }, 1500);
    };

    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      es.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [order?.id, order?.status, order?.verdict, refreshHistory, streamTick]);

  // ---- шаг 1: разбор заказа в план (ничего не выполняется) ------------
  const dryRun = useCallback(async (t?: string) => {
    const body = (t ?? text).trim();
    if (!body || busy) return;
    setBusy(true); setErr(null); setOrder(null);
    try {
      setPlan(await api<Plan>('/api/order/plan', {
        method: 'POST',
        body: JSON.stringify({ text: body }),
      }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [text, busy]);

  // ---- шаг 3: подтверждение и запуск -----------------------------------
  const start = useCallback(async () => {
    if (!plan || busy) return;
    setBusy(true); setErr(null);
    try {
      setOrder(await api<Order>('/api/order', {
        method: 'POST',
        body: JSON.stringify({ text: text.trim() }),
      }));
      setPlan(null); setText('');
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [plan, text, busy]);

  // ---- шаг 5: ответ оператора, граф продолжает с места остановки ------
  const sendAnswer = useCallback(async () => {
    if (!order || !answer.trim()) return;
    setBusy(true); setErr(null);
    try {
      setOrder(await api<Order>(`/api/order/${order.id}/resume`, {
        method: 'POST',
        body: JSON.stringify({ answer: answer.trim() }),
      }));
      setAnswer('');
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [order, answer, busy]);

  const waiting = order?.verdict === 'needs_human';

  return (
    <div className="max-w-6xl mx-auto px-4 md:px-6 py-6 space-y-6">
      <header>
        <h2 className="font-display text-xl tracking-wide text-zone-900">
          ДОСЬЕ ЗАКАЗА · ORDER DOSSIER
        </h2>
        <p className="font-mono text-[10px] uppercase tracking-widest text-zone-600">
          движок {caps?.engine ?? '—'} · планировщик{' '}
          {plan ? plan.planner : caps?.planner === 'llm' ? 'LLM' : 'правила'}
          {caps?.runners?.length ? ` · сборка: ${caps.runners.join(', ')}` : ''}
        </p>
      </header>

      {/* ---------- форма ---------- */}
      <div className="border border-zone-300 bg-zone-100 rounded-lg p-4 space-y-3">
        <div className="flex gap-2">
          <textarea
            value={text}
            onChange={(e) => { setText(e.target.value); setPlan(null); }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) dryRun();
            }}
            rows={2}
            placeholder="Опишите задачу: «собери страницу отчёта», «посчитай дальность и пеленгацию»"
            className="flex-1 px-3 py-2 bg-white border border-zone-300 rounded text-sm resize-y"
          />
          <button
            onClick={() => dryRun()}
            disabled={busy || !text.trim()}
            className="px-4 rounded bg-zone-600 text-white text-sm hover:bg-zone-700 disabled:opacity-40 self-end flex items-center gap-2"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ListTree className="w-4 h-4" />}
            Разобрать
          </button>
        </div>

        {caps && caps.suggestions.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {caps.suggestions.map((s) => (
              <button
                key={s}
                onClick={() => { setText(s); dryRun(s); }}
                className="px-2.5 py-1 text-[11px] rounded border border-zone-300 text-zone-600 hover:border-zone-500 hover:text-zone-900"
              >
                {s}
              </button>
            ))}
          </div>
        )}
        {err && <p className="text-xs text-blood-600">{err}</p>}
      </div>

      {/* ---------- план на утверждение ---------- */}
      {plan && (
        <section className="border border-zone-400 bg-zone-100 rounded-lg overflow-hidden">
          <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-zone-300">
            <div className="flex items-center gap-2 min-w-0">
              <ListTree className="w-4 h-4 text-zone-700 shrink-0" />
              <span className="font-medium text-sm text-zone-900">План</span>
              <span className="font-mono text-[10px] text-zone-600">{plan.intent}</span>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <button
                onClick={() => setPlan(null)}
                className="px-2.5 py-1 text-[11px] rounded border border-zone-300 text-zone-600 hover:border-zone-500 flex items-center gap-1"
              >
                <Ban className="w-3 h-3" /> отмена
              </button>
              <button
                onClick={start}
                disabled={busy || plan.targets.length === 0}
                className="px-3 py-1 text-[11px] rounded bg-blood-500 text-white hover:bg-blood-600 disabled:opacity-40 flex items-center gap-1"
              >
                <Play className="w-3 h-3" /> выполнить
              </button>
            </div>
          </div>

          {plan.targets.length === 0 ? (
            <p className="px-4 py-3 text-sm text-zone-700">
              Заказ не сопоставлен ни с одной целью. Доступно:{' '}
              <span className="font-mono text-xs text-zone-900">
                {(plan.available ?? []).slice(0, 12).join(', ')}
              </span>
            </p>
          ) : (
            <ol className="divide-y divide-zone-200">
              {plan.targets.map((t, i) => (
                <li key={t} className="flex items-center gap-3 px-4 py-2 text-sm">
                  <span className="font-mono text-[10px] text-zone-500 w-5">{i + 1}</span>
                  <code className="font-mono text-zone-900">{t}</code>
                </li>
              ))}
            </ol>
          )}
          <p className="px-4 py-2 border-t border-zone-300 font-mono text-[10px] text-zone-600">
            {plan.note} · зависимости подтянет система сборки
          </p>
        </section>
      )}

      {/* ---------- текущий заказ ---------- */}
      {order && (
        <section className="border border-zone-300 bg-zone-100 rounded-lg overflow-hidden">
          <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-zone-300">
            <div className="flex items-center gap-2 min-w-0">
              <Cpu className="w-4 h-4 text-blood-500 shrink-0" />
              <span className="font-medium text-sm text-zone-900 truncate">
                {order.text}
              </span>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <span className="font-mono text-[10px] text-zone-600">
                {order.intent}
              </span>
              <span
                className={`font-mono text-[10px] uppercase px-2 py-0.5 rounded ${
                  order.status === 'done' ? 'bg-emerald-100 text-emerald-700'
                  : order.status === 'running' ? 'bg-sky-100 text-sky-700'
                  : order.status === 'queued' ? 'bg-zone-200 text-zone-600'
                  : 'bg-amber-100 text-amber-700'}`}
              >
                {order.status}
              </span>
              <span className="font-mono text-xs text-zone-900 w-10 text-right">
                {order.progress}%
              </span>
            </div>
          </div>

          <div className="h-1 bg-zone-200">
            <div
              className="h-full bg-blood-500 transition-all duration-500"
              style={{ width: `${order.progress}%` }}
            />
          </div>

          <ol className="divide-y divide-zone-200">
            {order.steps.map((s) => {
              const Icon = STATUS_ICON[s.status];
              return (
                <li key={s.id} className="px-4 py-2.5">
                  <div className="flex items-center gap-3">
                    <Icon className={`w-4 h-4 shrink-0 ${STATUS_COLOR[s.status]} ${
                      s.status === 'running' ? 'animate-spin' : ''}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-sm text-zone-900 truncate">{s.title}</span>
                        <code className="font-mono text-[10px] text-zone-500 bg-zone-200 px-1 rounded shrink-0">
                          make {s.target}
                        </code>
                      </div>
                      {s.error && (
                        <p className="text-[11px] text-blood-500 truncate">{s.error}</p>
                      )}
                    </div>
                    <span className="flex items-center gap-1.5 shrink-0">
                      <Cpu className="w-3.5 h-3.5 text-zone-500" />
                      <span className="font-mono text-[11px] text-zone-700">
                        {s.agent_id.replace('agent.', '')}
                      </span>
                    </span>
                    {s.exit_code !== null && (
                      <span className="font-mono text-[10px] text-zone-500 w-10 text-right shrink-0">
                        exit {s.exit_code}
                      </span>
                    )}
                  </div>
                  {showLog && s.log_tail && s.log_tail.length > 0 && (
                    <pre className="mt-2 ml-7 font-mono text-[10px] leading-relaxed text-zone-600 whitespace-pre-wrap max-h-40 overflow-y-auto">
                      {s.log_tail.slice(-12).join('\n')}
                    </pre>
                  )}
                </li>
              );
            })}
          </ol>

          {/* ---------- вопрос оператору: граф прерван ---------- */}
          {waiting && (
            <div className="border-t border-amber-300 bg-amber-50 px-4 py-3 space-y-2">
              <div className="flex items-center gap-1.5 font-mono text-[9px] uppercase text-amber-800">
                <HelpCircle className="w-3.5 h-3.5" /> граф ждёт ответа
              </div>
              <p className="text-sm text-zone-900">{order.question}</p>
              <div className="flex gap-2">
                <input
                  value={answer}
                  onChange={(e) => setAnswer(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') sendAnswer(); }}
                  placeholder="Ваш ответ — работа продолжится с этого места"
                  className="flex-1 px-3 py-1.5 text-sm bg-white border border-amber-300 rounded"
                />
                <button
                  onClick={sendAnswer}
                  disabled={busy || !answer.trim()}
                  className="px-3 py-1.5 text-sm rounded bg-amber-600 text-white hover:bg-amber-700 disabled:opacity-40"
                >
                  Ответить
                </button>
              </div>
            </div>
          )}

          {/* ---------- живая консоль заказа ---------- */}
          <OrderConsole logs={order.logs} running={order.status === 'running'} />

          {/* ---------- сданный проект: файлы (все агенты) ---------- */}
          {order.status === 'done' && artifacts && artifacts.length > 0 && (
            <div className="border-t border-zone-300 px-4 py-4 bg-zone-50/60">
              <div className="flex items-center gap-2 mb-3">
                <Package className="w-4 h-4 text-blood-500" />
                <span className="font-display text-sm uppercase tracking-wide text-zone-900">
                  Результаты · Delivered Artifact Dossier
                </span>
                <span className="font-mono text-[10px] text-zone-600">
                  {artifacts.length} файлов — отчёт, 3D-модель, PCB, чертежи
                </span>
              </div>
              {/* Мини-превью (3D/SVG) */}
              <div className="flex flex-wrap gap-2 mb-3">
                {artifacts.map((a) => (
                  a.ext === '.svg' || a.ext === '.png' || a.ext === '.jpg' ? (
                    <div key={`preview-${a.url}`} className="rounded border border-zone-300 bg-white overflow-hidden shadow-sm">
                      <img src={`${BASE}${a.url}`} alt={a.name} className="h-24 w-auto object-contain" />
                    </div>
                  ) : a.ext === '.glb' ? (
                    <a key={`preview-${a.url}`} href={`${BASE}${a.url}`} target="_blank" rel="noreferrer" className="px-3 py-2 rounded border border-blood-500 bg-blood-50 text-blood-600 text-xs font-mono flex items-center gap-2">
                      <Download className="w-3 h-3" /> открыть 3D {a.name}
                    </a>
                  ) : null
                )).filter(Boolean)}
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {artifacts.map((a) => (
                  <a
                    key={a.url}
                    href={`${BASE}${a.url}`}
                    target="_blank"
                    rel="noreferrer"
                    download
                    className="flex items-center gap-3 px-3 py-2 rounded border border-zone-300 bg-white hover:border-blood-500 hover:shadow-sm transition"
                  >
                    <span
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ background: a.ext === '.glb' ? '#ef4444' : a.ext === '.svg' ? '#3b82f6' : a.ext === '.pdf' ? '#f59e0b' : '#64748b' }}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm text-zone-900 truncate font-mono">{a.name}</div>
                      <div className="font-mono text-[9px] text-zone-500 uppercase">{a.size_h} · {a.ext.toUpperCase().slice(1) || 'FILE'}</div>
                    </div>
                    <span className="font-mono text-[9px] uppercase text-blood-500 flex items-center gap-1">
                      <Download className="w-3 h-3" /> получить
                    </span>
                  </a>
                ))}
              </div>
              <p className="font-mono text-[9px] text-zone-500 mt-2">
                В каждый архив — по одной ссылке; агенты: запросили цели → собрали → вышли с этим списком при завершении заказа.
              </p>
            </div>
          )}

          {order.report && (
            <div className="border-t border-zone-300 px-4 py-3">
              <div className="flex items-center gap-1.5 font-mono text-[9px] uppercase text-zone-600 mb-1.5">
                <Sparkles className="w-3 h-3" /> Отчёт
              </div>
              <pre className="font-mono text-[11px] leading-relaxed text-zone-700 whitespace-pre-wrap">
                {order.report}
              </pre>
            </div>
          )}
        </section>
      )}

      {/* ---------- история ---------- */}
      {history.length > 0 && (
        <section>
          <h3 className="font-mono text-[11px] uppercase tracking-widest text-zone-600 mb-2">
            История заказов
          </h3>
          <div className="space-y-1">
            {history.map((o) => (
              <button
                key={o.id}
                onClick={() => { setOrder(o); setPlan(null); }}
                className="w-full flex items-center gap-3 px-3 py-2 rounded border border-zone-300 bg-zone-100 hover:border-zone-500 text-left"
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                    o.status === 'done' ? 'bg-emerald-500'
                    : o.status === 'running' ? 'bg-sky-400 animate-pulse'
                    : 'bg-amber-500'}`}
                />
                <span className="flex-1 text-sm text-zone-800 truncate">{o.text}</span>
                <span className="font-mono text-[10px] text-zone-500">{o.intent}</span>
                <span className="flex items-center gap-1 font-mono text-[10px] text-zone-600 shrink-0">
                  <Clock className="w-3 h-3" />
                  {new Date(o.created_at).toLocaleTimeString('ru-RU')}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}