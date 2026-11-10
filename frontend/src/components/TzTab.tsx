/**
 * TzTab — вкладка «ТЗ»: техническое задание проекта.
 *
 * Бэкенд разбирает technical_task.md на секции (GET /api/tz) и
 * заодно отдаёт机械-требования из mech_spec.py — что конструкторская
 * часть делегируется инженеру, а не генерируется молча.
 *
 * Ничего не редактируется здесь: ТЗ — источник истины, вкладка
 * только читает и навигирует по нему.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  FileText, Wrench, RefreshCw, Loader2, ChevronRight, Search,
} from 'lucide-react';
import { getTz, type TzPayload } from '@/lib/bridge';

export function TzTab() {
  const [tz, setTz] = useState<TzPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [active, setActive] = useState(0);
  const [q, setQ] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      const d = await getTz();
      setTz(d);
      setErr(null);
      // первая содержательная секция (не общий заголовок)
      const i = d.sections.findIndex((s) => s.chars > 200);
      setActive(i >= 0 ? i : 0);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  // фильтрация секций по запросу (по заголовку и телу)
  const filtered = useMemo(() => {
    if (!tz) return [];
    if (!q.trim()) return tz.sections.map((s, i) => ({ s, i }));
    const needle = q.toLowerCase();
    return tz.sections
      .map((s, i) => ({ s, i }))
      .filter(({ s }) =>
        s.title.toLowerCase().includes(needle) ||
        s.body.toLowerCase().includes(needle));
  }, [tz, q]);

  if (loading) {
    return (
      <div className="max-w-7xl mx-auto px-4 md:px-6 py-10 flex items-center gap-3 text-zone-600">
        <Loader2 className="w-4 h-4 animate-spin" /> Загрузка ТЗ…
      </div>
    );
  }

  if (err || !tz) {
    return (
      <div className="max-w-3xl mx-auto p-8">
        <div className="bg-zone-100 border border-zone-300 rounded-lg p-6">
          <div className="flex items-center gap-3 text-blood-500 font-semibold mb-3">
            <FileText className="w-5 h-5" /> ТЗ недоступно: {err ?? 'нет данных'}
          </div>
          <button onClick={load} className="flex items-center gap-2 text-sm text-blood-500 hover:underline">
            <RefreshCw className="w-4 h-4" /> Повторить
          </button>
        </div>
      </div>
    );
  }

  const cur = tz.sections[active];
  const mech = tz.mech;

  return (
    <div className="max-w-7xl mx-auto px-4 md:px-6 py-6 space-y-6">

      {/* ============ ШАПКА ============ */}
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="font-display text-xl tracking-wide text-zone-900">
            ТЕХНИЧЕСКОЕ ЗАДАНИЕ
          </h2>
          <p className="font-mono text-[10px] uppercase tracking-widest text-zone-600">
            {tz.file} · {tz.section_count} секций · {tz.chars.toLocaleString('ru-RU')} зн.
          </p>
        </div>
        <div className="relative">
          <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-zone-500" />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Поиск по ТЗ…"
            className="pl-8 pr-3 py-1.5 bg-white border border-zone-300 rounded text-sm font-mono w-56" />
        </div>
      </header>

      {/* ============ МЕХ-ТРЕБОВАНИЯ (делегирование конструктору) ============ */}
      {mech?.has_mechanical && (
        <section className="border border-amber-300 bg-amber-50 rounded-lg p-4">
          <div className="flex items-center gap-2 mb-2">
            <Wrench className="w-4 h-4 text-amber-600" />
            <h3 className="font-mono text-[11px] uppercase tracking-widest text-amber-800">
              Конструкторская часть — делегируется инженеру
            </h3>
          </div>
          <p className="text-sm text-amber-900 mb-2">
            В ТЗ найдено {mech.requirement_count} механических требований
            ({(mech.categories ?? []).join(', ')}). Корпус и конструкция
            ведутся в FreeCAD — автоматика только предлагает и проверяет.
          </p>
          <div className="flex flex-wrap gap-2">
            {(mech.requirements ?? []).slice(0, 8).map((r, i) => (
              <span key={i}
                className="font-mono text-[10px] bg-amber-100 border border-amber-300 text-amber-900 px-2 py-0.5 rounded">
                {r.label} · стр. {r.line}
              </span>
            ))}
            {(mech.requirements?.length ?? 0) > 8 && (
              <span className="font-mono text-[10px] text-amber-700">
                +{(mech.requirements!.length - 8)}
              </span>
            )}
          </div>
          <p className="mt-2 font-mono text-[10px] text-amber-700">
            бриф: <code className="bg-amber-100 px-1 rounded">make cad-brief</code> →
            out/mech_constructor_brief.md
          </p>
        </section>
      )}

      {/* ============ НАВИГАЦИЯ + ТЕЛО ============ */}
      <div className="grid lg:grid-cols-[280px_1fr] gap-4">
        {/* оглавление */}
        <nav className="border border-zone-200 rounded-lg bg-zone-50 overflow-hidden self-start max-h-[70vh] overflow-y-auto">
          {filtered.map(({ s, i }) => (
            <button key={s.n} onClick={() => setActive(i)}
              className={`w-full text-left px-3 py-2 border-b border-zone-200 transition-colors
                ${i === active ? 'bg-blood-50 text-blood-600' : 'hover:bg-zone-100 text-zone-700'}`}>
              <div className="flex items-start gap-1.5">
                <ChevronRight className={`w-3 h-3 mt-0.5 shrink-0 ${i === active ? 'text-blood-500' : 'text-zone-400'}`} />
                <span className={`text-[12px] leading-snug ${s.level <= 2 ? 'font-semibold' : ''}`}>
                  {s.title}
                </span>
              </div>
            </button>
          ))}
          {filtered.length === 0 && (
            <div className="px-3 py-4 text-[12px] text-zone-500">
              Ничего не найдено по «{q}»
            </div>
          )}
        </nav>

        {/* содержимое секции */}
        <article className="border border-zone-200 rounded-lg bg-white p-5 min-h-[40vh]">
          {cur ? (
            <>
              <div className="flex items-baseline justify-between gap-3 border-b border-zone-200 pb-2 mb-3">
                <h3 className="font-display text-lg text-zone-900">{cur.title}</h3>
                <span className="font-mono text-[10px] text-zone-400 shrink-0">
                  {cur.chars} зн.
                </span>
              </div>
              <div className="prose-sm max-w-none">
                {cur.body.split(/\n{2,}/).map((para, i) => (
                  <p key={i} className="text-[13px] leading-relaxed text-zone-800 mb-3 whitespace-pre-wrap">
                    {para}
                  </p>
                ))}
                {!cur.body && (
                  <p className="text-sm text-zone-500 italic">Раздел без описания (только заголовок).</p>
                )}
              </div>
            </>
          ) : (
            <p className="text-sm text-zone-500">Выберите раздел слева.</p>
          )}
        </article>
      </div>

      {/* варианты ТЗ */}
      {tz.variants.length > 0 && (
        <p className="font-mono text-[10px] text-zone-500">
          другие версии: {tz.variants.join(' · ')}
        </p>
      )}
    </div>
  );
}
