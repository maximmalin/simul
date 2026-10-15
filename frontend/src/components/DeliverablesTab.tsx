import { useEffect, useState } from 'react';
import { Download, FileText, FileCode, FileJson, Package, AlertCircle, Search } from 'lucide-react';
import { supabase } from '@/lib/supabase';
import type { DeliverableWithRelations } from '@/lib/supabase';
import { ROLE_ICONS } from '@/lib/constants';

const FORMAT_ICONS: Record<string, typeof FileText> = {
  txt: FileText,
  md: FileText,
  json: FileJson,
  csv: FileCode,
};

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string; border: string }> = {
  draft: { label: 'Draft', color: 'text-zone-600', bg: 'bg-zone-400/15', border: 'border-zone-500/30' },
  final: { label: 'Final', color: 'text-amber-400', bg: 'bg-amber-600/15', border: 'border-amber-500/30' },
  published: { label: 'Published', color: 'text-toxic-400', bg: 'bg-toxic-700/15', border: 'border-toxic-600/30' },
};

export function DeliverablesTab() {
  const [deliverables, setDeliverables] = useState<DeliverableWithRelations[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterStatus, setFilterStatus] = useState('all');

  useEffect(() => {
    fetchDeliverables();
  }, []);

  const fetchDeliverables = async () => {
    const { data } = await supabase
      .from('bureau_deliverables')
      .select(`
        *,
        project:bureau_projects(id, code, title),
        agent:bureau_agents!agent_id(id, codename, zone_callsign, role_key)
      `)
      .order('created_at', { ascending: false });

    setDeliverables((data ?? []) as unknown as DeliverableWithRelations[]);
    setLoading(false);
  };

  const handleDownload = (deliv: DeliverableWithRelations) => {
    const blob = new Blob([deliv.file_content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const safeTitle = deliv.title.replace(/[^a-zA-Z0-9-_]/g, '_').slice(0, 60);
    a.download = `${safeTitle}.${deliv.file_format}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const filtered = deliverables.filter((d) => {
    const matchesSearch = !searchQuery ||
      d.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      d.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = filterStatus === 'all' || d.status === filterStatus;
    return matchesSearch && matchesStatus;
  });

  return (
    <div className="px-4 md:px-6 py-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-5">
        <div className="w-8 h-8 bg-toxic-600 flex items-center justify-center clip-corner-tl">
          <Package className="w-4 h-4 text-zone-900" />
        </div>
        <div>
          <h2 className="font-display font-600 text-xl uppercase tracking-tight text-zone-900">
            Deliverables Archive
          </h2>
          <p className="font-mono text-[10px] tracking-widest text-zone-600 uppercase">
            Final Results · Download Bureau Output
          </p>
        </div>
        <div className="ml-auto font-mono text-[10px] text-zone-600 tracking-widest uppercase">
          {deliverables.length} Items
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-col md:flex-row gap-2 mb-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zone-600" />
          <input
            type="text"
            placeholder="Search deliverables..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full text-sm bg-zone-200 border border-zone-400/20 rounded-sm pl-9 pr-3 py-2 text-zone-900 placeholder-zone-600 focus:outline-none focus:border-blood-500"
          />
        </div>
        <div className="flex gap-1.5">
          {['all', 'published', 'final', 'draft'].map((s) => (
            <button
              key={s}
              onClick={() => setFilterStatus(s)}
              className={`font-mono text-[10px] uppercase tracking-widest px-3 py-2 rounded-sm border transition-all
                ${filterStatus === s
                  ? 'bg-blood-600/20 border-blood-500/40 text-blood-500 font-bold'
                  : 'bg-zone-200 border-zone-400/20 text-zone-600 hover:text-zone-800'}`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Deliverables grid */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-blood-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-zone-600">
          <AlertCircle className="w-8 h-8 mb-2" />
          <p className="font-mono text-xs uppercase tracking-widest">No deliverables found</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {filtered.map((deliv) => {
            const FormatIcon = FORMAT_ICONS[deliv.file_format] ?? FileText;
            const statusCfg = STATUS_CONFIG[deliv.status] ?? STATUS_CONFIG.draft;
            const agentRoleKey = deliv.agent?.role_key ?? '';
            const AgentIcon = agentRoleKey ? ROLE_ICONS[agentRoleKey] ?? FileText : FileText;
            const fileSize = new Blob([deliv.file_content]).size;
            const sizeStr = fileSize > 1024 ? `${(fileSize / 1024).toFixed(1)} KB` : `${fileSize} B`;

            return (
              <div
                key={deliv.id}
                className="bg-zone-panel border border-zone-400/20 clip-corner overflow-hidden hover:border-zone-500/40 transition-all"
              >
                {/* Top bar */}
                <div className="flex items-center gap-2 px-4 py-2.5 border-b border-zone-400/15 bg-zone-200/20">
                  <FormatIcon className="w-4 h-4 text-zone-600 flex-shrink-0" />
                  <span className="font-mono text-[9px] tracking-widest text-zone-600 uppercase">
                    {deliv.file_format.toUpperCase()}
                  </span>
                  <span className="font-mono text-[9px] text-zone-600/50">·</span>
                  <span className="font-mono text-[9px] text-zone-600">{sizeStr}</span>
                  <div className="ml-auto flex items-center gap-1.5">
                    <span className={`px-2 py-0.5 text-[8px] font-mono uppercase tracking-widest rounded-sm border ${statusCfg.bg} ${statusCfg.color} ${statusCfg.border}`}>
                      {statusCfg.label}
                    </span>
                  </div>
                </div>

                {/* Body */}
                <div className="p-4">
                  <h3 className="font-display font-600 text-sm uppercase tracking-tight text-zone-900 mb-1">
                    {deliv.title}
                  </h3>
                  <p className="text-xs text-zone-600 leading-snug mb-3">
                    {deliv.description}
                  </p>

                  {/* Meta */}
                  <div className="flex items-center gap-3 mb-3 pb-3 border-b border-zone-400/15">
                    {deliv.agent && (
                      <div className="flex items-center gap-1.5">
                        <div className="w-5 h-5 bg-zone-300/30 flex items-center justify-center rounded-sm">
                          <AgentIcon className="w-3 h-3 text-zone-700" />
                        </div>
                        <span className="font-mono text-[10px] text-zone-700 tracking-wider">
                          {deliv.agent.codename}
                        </span>
                        <span className="font-mono text-[9px] text-zone-600">
                          · {deliv.agent.zone_callsign}
                        </span>
                      </div>
                    )}
                    {deliv.project && (
                      <div className="flex items-center gap-1">
                        <span className="font-mono text-[10px] text-blood-500/80 tracking-wider">
                          {deliv.project.code}
                        </span>
                      </div>
                    )}
                    <span className="font-mono text-[9px] text-zone-600 ml-auto">
                      {new Date(deliv.created_at).toLocaleDateString('en-GB')}
                    </span>
                  </div>

                  {/* Preview + download */}
                  <div className="flex items-start gap-2">
                    <div className="flex-1 bg-zone-100/50 border border-zone-400/15 rounded-sm p-2 max-h-20 overflow-hidden">
                      <pre className="font-mono text-[9px] text-zone-600 leading-tight whitespace-pre-wrap break-words">
                        {deliv.file_content.slice(0, 200)}
                        {deliv.file_content.length > 200 && '...'}
                      </pre>
                    </div>
                    <button
                      onClick={() => handleDownload(deliv)}
                      className="flex-shrink-0 flex flex-col items-center gap-1 bg-blood-600/20 border border-blood-500/30 hover:bg-blood-600/30 hover:border-blood-500/50 transition-all px-3 py-2 rounded-sm clip-corner-tl"
                    >
                      <Download className="w-4 h-4 text-blood-500" />
                      <span className="font-mono text-[8px] uppercase tracking-widest text-blood-500">
                        Download
                      </span>
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
