import { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronUp, Eraser, ScrollText, Terminal } from 'lucide-react';

interface ConsoleLine {
  t: string;
  text: string;
  level: 'info' | 'stage' | 'agent' | 'good' | 'warn' | 'bad';
}

/** Классика строки журнала заказа — только по виду строки, без знания
 *  о содержимом устройства. */
function classify(raw: string): ConsoleLine['level'] {
  const s = raw.toLowerCase();
  if (s.includes('не выполнено') || s.includes('ошибк') || s.includes('провал')) return 'bad';
  if (s.includes('сторож') || s.includes('эскалац') || s.includes('отклонена') || s.includes('не хватило')) return 'warn';
  if (s.includes('опубликовано') || s.includes('архив') || s.includes('записан') || s.includes('замечания нет')) return 'good';
  if (s.includes('стадии') || s.includes('конвейер запущен') || s.includes('песочница')) return 'stage';
  if (s.includes('агент →') || s.includes('лог агентов')) return 'agent';
  return 'info';
}

const TONE: Record<ConsoleLine['level'], string> = {
  info: 'text-zone-600',
  stage: 'text-cyan-600 font-semibold',
  agent: 'text-violet-600',
  good: 'text-emerald-600',
  warn: 'text-amber-600',
  bad: 'text-red-600 font-semibold',
};

export function OrderConsole({
  logs,
  running,
}: {
  logs?: string[];
  running: boolean;
}) {
  const lines: ConsoleLine[] = (logs ?? []).map((raw) => {
    const m = /^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$/.exec(raw);
    return {
      t: m ? m[1] : '',
      text: m ? m[2] : raw,
      level: classify(m ? m[2] : raw),
    };
  });
  // консоль открыта сразу после старта заказа; сворачивается вручную
  const [open, setOpen] = useState(true);
  const [follow, setFollow] = useState(true);
  const boxRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (open && follow && boxRef.current) {
      boxRef.current.scrollTop = boxRef.current.scrollHeight;
    }
  }, [lines.length, open, follow]);

  if (!lines.length) return null;

  return (
    <div className="border-t border-zone-300 bg-zinc-950">
      <div className="flex items-center gap-2 px-3 py-1.5">
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 font-mono text-[9px] uppercase text-zinc-400 hover:text-zinc-200"
        >
          <Terminal className="w-3 h-3" />
          {open ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />}
          консоль ({lines.length})
          {running && (
            <span className="ml-1 inline-flex items-center gap-1 text-emerald-400">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              live
            </span>
          )}
        </button>
        <div className="flex-1" />
        {open && (
          <>
            <button
              onClick={() => setFollow((v) => !v)}
              className={`font-mono text-[9px] uppercase ${
                follow ? 'text-cyan-500' : 'text-zinc-500 hover:text-zinc-300'
              }`}
              title="следовать за новыми строками"
            >
              follow
            </button>
            <button
              onClick={() => {
                navigator.clipboard?.writeText(
                  lines.map((l) => (l.t ? `[${l.t}] ${l.text}` : l.text)).join('\n'),
                );
              }}
              className="flex items-center gap-1 font-mono text-[9px] uppercase text-zinc-500 hover:text-zinc-300"
              title="скопировать журнал"
            >
              <ScrollText className="w-3 h-3" />
              копия
            </button>
            <button
              onClick={() => setOpen(false)}
              className="font-mono text-[9px] uppercase text-zinc-500 hover:text-zinc-300"
              title="скрыть консоль"
            >
              <Eraser className="w-3 h-3" />
            </button>
          </>
        )}
      </div>

      {open && (
        <pre
          ref={boxRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
            if (atBottom !== follow) setFollow(atBottom);
          }}
          className="px-3 pb-3 pt-1 font-mono text-[10.5px] leading-relaxed overflow-y-auto max-h-72 whitespace-pre-wrap"
        >
          {lines.map((l, i) => (
            <div key={i} className="flex gap-2">
              <span className="text-zinc-600 shrink-0">{l.t}</span>
              <span className={TONE[l.level]}>{l.text}</span>
            </div>
          ))}
        </pre>
      )}
    </div>
  );
}