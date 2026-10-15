/**
 * OrderTab — приём заказа из интерфейса и его мультиагентная обработка.
 *
 * Пользователь пишет заказ обычным текстом («собери страницу отчёта»),
 * он уходит в backend по `POST /api/order`, где планировщик раскладывает
 * его на шаги и назначает агента, исполнитель гоняет шаги (независимые —
 * параллельно), а вкладка показывает прогресс по каждому агенту.
 *
 * Данные: агентный мост (backend/orchestrator.py), не Supabase.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Send, Cpu, CheckCircle2, XCircle, Loader2, Circle, Clock, Sparkles,
  ListTree, SkipForward,
} from 'lucide-react';

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
}

interface Order {
  id: string;
  text: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'partial';
  intent: string;
  planner: 'rules' | 'llm';
  progress: number;
  created_at: string;
  finished_at: string | null;
  steps: Step[];
  report: string;
}

interface Capabilities {
  planner: 'rules' | 'llm';
  max_parallel: number;
  intents: string[];
  targets: Record<string, string>;
  suggestions: { text: string; intent: string }[];
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

export function OrderTab() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [text, setText] = useState('');
  const [order, setOrder] = useState<Order | null>(null);
  const [history, setHistory] = useState<Order[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    api<Capabilities>('/api/order/capabilities')
      .then(setCaps)
      .catch(() => setErr('оркестратор недоступен — запустите make agent-api'));
    api<{ orders: Order[] }>('/api/orders?limit=10')
      .then((d) => setHistory(d.orders as unknown as Order[]))
      .catch(() => {});
  }, []);

  // опрос активного заказа
  useEffect(() => {
    if (!order || order.status === 'done' || order.status === 'failed'
        || order.status === 'partial') {
      if (timer.current) window.clearInterval(timer.current);
      return;
    }
    timer.current = window.setInterval(async () => {
      try {
        const o = await api<Order>(`/api/order/${order.id}`);
        setOrder(o);
        if (o.status !== 'running' && o.status !== 'queued') {
          window.clearInterval(timer.current!);
          api<{ orders: Order[] }>('/api/orders?limit=10')
            .then((d) => setHistory(d.orders as unknown as Order[]))
            .catch(() => {});
        }
      } catch { /* мост мог перезапуститься */ }
    }, 1200);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [order]);

  const submit = useCallback(async (t?: string) => {
    const body = (t ?? text).trim();
    if (!body || busy) return;
    setBusy(true); setErr(null);
    try {
      setOrder(await api<Order>('/api/order', {
        method: 'POST',
        body: JSON.stringify({ text: body }),
      }));
      setText('');
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [text, busy]);

  return (
    <div className="max-w-6xl mx-auto px-4 md:px-6 py-6 space-y-6">
      <header>
        <h2 className="font-display text-xl tracking-wide text-zone-900">
          ЗАКАЗ
        </h2>
        <p className="font-mono text-[10px] uppercase tracking-widest text-zone-600">
          мультиагентная обработка · планировщик{' '}
          {caps?.planner === 'llm' ? 'LLM' : 'правила'} · параллельность{' '}
          {caps?.max_parallel ?? '—'}
        </p>
      </header>

      {/* ---------- форма ---------- */}
      <div className="border border-zone-300 bg-zone-100 rounded-lg p-4 space-y-3">
        <div className="flex gap-2">
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit(); }}
            rows={2}
            placeholder="Опишите задачу: «собери страницу отчёта», «посчитай дальность и пеленгацию», «собери всё и проверь тестами»"
            className="flex-1 px-3 py-2 bg-white border border-zone-300 rounded text-sm resize-y"
          />
          <button
            onClick={() => submit()}
            disabled={busy || !text.trim()}
            className="px-4 rounded bg-blood-500 text-white text-sm hover:bg-blood-600 disabled:opacity-40 self-end flex items-center gap-2"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            Отправить
          </button>
        </div>

        {caps && caps.suggestions.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {caps.suggestions.map((s) => (
              <button
                key={s.text}
                onClick={() => submit(s.text)}
                className="px-2.5 py-1 text-[11px] rounded border border-zone-300 text-zone-600 hover:border-zone-500 hover:text-zone-900"
              >
                {s.text}
              </button>
            ))}
          </div>
        )}
        {err && <p className="text-xs text-blood-600">{err}</p>}
      </div>

      {/* ---------- текущий заказ ---------- */}
      {order && (
        <section className="border border-zone-300 bg-zone-100 rounded-lg overflow-hidden">
          <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-zone-300">
            <div className="flex items-center gap-2 min-w-0">
              <ListTree className="w-4 h-4 text-blood-500 shrink-0" />
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
                <li key={s.id} className="flex items-center gap-3 px-4 py-2.5">
                  <Icon className={`w-4 h-4 shrink-0 ${STATUS_COLOR[s.status]} ${
                    s.status === 'running' ? 'animate-spin' : ''}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm text-zone-900">{s.title}</span>
                      <code className="font-mono text-[10px] text-zone-500 bg-zone-200 px-1 rounded">
                        {s.kind === 'make' ? `make ${s.target}` : s.target}
                      </code>
                    </div>
                    {s.error && (
                      <p className="text-[11px] text-blood-500 truncate">{s.error}</p>
                    )}
                  </div>
                  <span className="flex items-center gap-1.5 shrink-0">
                    <Cpu className="w-3.5 h-3.5 text-zone-500" />
                    <span className="font-mono text-[11px] text-zone-700">
                      {s.agent_id.replace('AGENT-', '')}
                    </span>
                  </span>
                  {s.exit_code !== null && (
                    <span className="font-mono text-[10px] text-zone-500 w-10 text-right">
                      exit {s.exit_code}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>

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
                onClick={() => setOrder(o)}
                className="w-full flex items-center gap-3 px-3 py-2 rounded border border-zone-300 bg-zone-100 hover:border-zone-500 text-left"
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                    o.status === 'done' ? 'bg-emerald-500'
                    : o.status === 'running' ? 'bg-sky-400 animate-pulse'
                    : 'bg-amber-500'}`}
                />
                <span className="flex-1 text-sm text-zone-800 truncate">{o.text}</span>
                <span className="font-mono text-[10px] text-zone-500">
                  {o.intent}
                </span>
                <span className="flex items-center gap-1 font-mono text-[10px] text-zone-600">
                  <Clock className="w-3 h-3" />
                  {new Date(o.created_at).toLocaleTimeString('ru-RU')}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {/* ---------- возможности ---------- */}
      {caps && (
        <section>
          <h3 className="font-mono text-[11px] uppercase tracking-widest text-zone-600 mb-2">
            Доступные цели ({Object.keys(caps.targets).length})
          </h3>
          <div className="grid sm:grid-cols-2 gap-x-4 gap-y-1">
            {Object.entries(caps.targets).map(([t, d]) => (
              <div key={t} className="flex items-baseline gap-2 text-[11px]">
                <code className="font-mono text-zone-900 w-24 shrink-0">{t}</code>
                <span className="text-zone-600 truncate">{d}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}