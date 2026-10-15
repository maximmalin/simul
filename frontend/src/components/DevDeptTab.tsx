import { useEffect, useState, useCallback } from 'react';
import {
  Code2, GitBranch, GitCommit,
  Plus, X, Save, Loader2, Trash2, Edit3,
  CheckCircle2, AlertCircle,
} from 'lucide-react';
import { supabase } from '@/lib/supabase';
import type { DevAgent, DevAgentStatus } from '@/lib/supabase';
import {
  DEV_ROLE_ICONS, DEV_ROLE_LABELS, DEV_STATUS_CONFIG,
} from '@/lib/constants';

const ROLE_KEYS = ['lead_dev', 'frontend_dev', 'backend_dev', 'devops_eng', 'qa_eng', 'sys_arch'];

const ALL_LANGUAGES = ['TypeScript', 'JavaScript', 'Python', 'Rust', 'Go', 'SQL', 'Bash', 'YAML', 'Dockerfile', 'CSS'];
const ALL_FRAMEWORKS = ['React', 'Vite', 'Deno', 'Supabase', 'PostgreSQL', 'Tailwind', 'Vitest', 'Playwright', 'Docker', 'Tokio', 'Hono', 'Zustand', 'Redis', 'GitHub Actions'];

export function DevDeptTab() {
  const [devs, setDevs] = useState<DevAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [filterRole, setFilterRole] = useState('all');

  const [formData, setFormData] = useState({
    codename: '',
    role_key: 'frontend_dev',
    role_title: '',
    designation: '',
    status: 'idle' as DevAgentStatus,
    specialty: '',
    languages: [] as string[],
    frameworks: [] as string[],
    current_project: '',
    git_branch: '',
    commits_today: 0,
    prs_open: 0,
    code_quality: 85,
    efficiency: 85,
  });

  const fetchDevs = useCallback(async () => {
    const { data } = await supabase.from('dev_agents').select('*').order('created_at');
    setDevs((data ?? []) as DevAgent[]);
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchDevs();
  }, [fetchDevs]);

  const handleSave = async () => {
    if (!formData.codename.trim()) return;
    setSaveStatus('saving');

    try {
      if (editingId) {
        const { error } = await supabase
          .from('dev_agents')
          .update({
            codename: formData.codename,
            role_key: formData.role_key,
            role_title: formData.role_title || DEV_ROLE_LABELS[formData.role_key],
            designation: formData.designation,
            status: formData.status,
            specialty: formData.specialty,
            languages: formData.languages,
            frameworks: formData.frameworks,
            current_project: formData.current_project || null,
            git_branch: formData.git_branch || null,
            commits_today: formData.commits_today,
            prs_open: formData.prs_open,
            code_quality: formData.code_quality,
            efficiency: formData.efficiency,
          })
          .eq('id', editingId);
        if (error) throw error;
      } else {
        const { error } = await supabase
          .from('dev_agents')
          .insert({
            codename: formData.codename,
            role_key: formData.role_key,
            role_title: formData.role_title || DEV_ROLE_LABELS[formData.role_key],
            designation: formData.designation,
            status: formData.status,
            specialty: formData.specialty,
            languages: formData.languages,
            frameworks: formData.frameworks,
            current_project: formData.current_project || null,
            git_branch: formData.git_branch || null,
            commits_today: formData.commits_today,
            prs_open: formData.prs_open,
            code_quality: formData.code_quality,
            efficiency: formData.efficiency,
          });
        if (error) throw error;
      }

      setSaveStatus('saved');
      setShowForm(false);
      setEditingId(null);
      setFormData({
        codename: '', role_key: 'frontend_dev', role_title: '', designation: '',
        status: 'idle', specialty: '', languages: [], frameworks: [],
        current_project: '', git_branch: '', commits_today: 0, prs_open: 0,
        code_quality: 85, efficiency: 85,
      });
      await fetchDevs();
      setTimeout(() => setSaveStatus('idle'), 2000);
    } catch {
      setSaveStatus('error');
      setTimeout(() => setSaveStatus('idle'), 3000);
    }
  };

  const handleEdit = (dev: DevAgent) => {
    setEditingId(dev.id);
    setFormData({
      codename: dev.codename,
      role_key: dev.role_key,
      role_title: dev.role_title,
      designation: dev.designation,
      status: dev.status,
      specialty: dev.specialty,
      languages: dev.languages,
      frameworks: dev.frameworks,
      current_project: dev.current_project ?? '',
      git_branch: dev.git_branch ?? '',
      commits_today: dev.commits_today,
      prs_open: dev.prs_open,
      code_quality: dev.code_quality,
      efficiency: dev.efficiency,
    });
    setShowForm(true);
    setExpandedId(null);
  };

  const handleDelete = async (id: string) => {
    await supabase.from('dev_agents').delete().eq('id', id);
    setDevs((prev) => prev.filter((d) => d.id !== id));
  };

  const handleCancel = () => {
    setShowForm(false);
    setEditingId(null);
    setFormData({
      codename: '', role_key: 'frontend_dev', role_title: '', designation: '',
      status: 'idle', specialty: '', languages: [], frameworks: [],
      current_project: '', git_branch: '', commits_today: 0, prs_open: 0,
      code_quality: 85, efficiency: 85,
    });
  };

  const toggleLang = (lang: string) => {
    setFormData((prev) => ({
      ...prev,
      languages: prev.languages.includes(lang)
        ? prev.languages.filter((l) => l !== lang)
        : [...prev.languages, lang],
    }));
  };

  const toggleFramework = (fw: string) => {
    setFormData((prev) => ({
      ...prev,
      frameworks: prev.frameworks.includes(fw)
        ? prev.frameworks.filter((f) => f !== fw)
        : [...prev.frameworks, fw],
    }));
  };

  const filtered = filterRole === 'all' ? devs : devs.filter((d) => d.role_key === filterRole);
  const totalCommits = devs.reduce((sum, d) => sum + d.commits_today, 0);
  const totalPRs = devs.reduce((sum, d) => sum + d.prs_open, 0);
  const avgQuality = devs.length > 0 ? Math.round(devs.reduce((sum, d) => sum + d.code_quality, 0) / devs.length) : 0;

  return (
    <div className="px-4 md:px-6 py-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-5">
        <div className="w-8 h-8 bg-steel-600 flex items-center justify-center clip-corner-tl">
          <Code2 className="w-4 h-4 text-zone-900" />
        </div>
        <div>
          <h2 className="font-display font-600 text-xl uppercase tracking-tight text-zone-900">
            Development Department
          </h2>
          <p className="font-mono text-[10px] tracking-widest text-zone-600 uppercase">
            Отдел Программистов · Engineers · Code Pipeline
          </p>
        </div>
        <div className="ml-auto flex items-center gap-4">
          <div className="text-right">
            <div className="font-mono text-[10px] text-zone-600 uppercase tracking-wider">{devs.length} Devs</div>
            <div className="font-mono text-[10px] text-toxic-400 tracking-wider">{totalCommits} Commits Today</div>
          </div>
          <button
            onClick={() => { setEditingId(null); handleCancel(); setShowForm(true); }}
            className="flex items-center gap-1.5 bg-blood-600/20 border border-blood-500/30 hover:bg-blood-600/30 transition-all px-3 py-2 rounded-sm clip-corner-tl"
          >
            <Plus className="w-4 h-4 text-blood-500" />
            <span className="font-mono text-[10px] uppercase tracking-widest text-blood-500">Add Dev</span>
          </button>
        </div>
      </div>

      {/* Stats bar */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
        <StatCard icon={GitCommit} label="Commits Today" value={totalCommits.toString()} color="text-toxic-400" />
        <StatCard icon={GitBranch} label="Open PRs" value={totalPRs.toString()} color="text-amber-400" />
        <StatCard icon={CheckCircle2} label="Avg Code Quality" value={`${avgQuality}%`} color="text-steel-400" />
        <StatCard icon={Code2} label="Active Devs" value={devs.filter((d) => d.status === 'coding' || d.status === 'active').length.toString()} color="text-blood-500" />
      </div>

      {/* Role filter */}
      <div className="flex flex-wrap gap-1.5 mb-4">
        <button
          onClick={() => setFilterRole('all')}
          className={`font-mono text-[9px] uppercase tracking-widest px-2.5 py-1 rounded-sm border transition-all
            ${filterRole === 'all' ? 'bg-blood-600/20 border-blood-500/40 text-blood-500 font-bold' : 'bg-zone-200 border-zone-400/20 text-zone-600 hover:text-zone-800'}`}
        >
          All ({devs.length})
        </button>
        {ROLE_KEYS.map((role) => {
          const count = devs.filter((d) => d.role_key === role).length;
          if (count === 0) return null;
          return (
            <button
              key={role}
              onClick={() => setFilterRole(role)}
              className={`font-mono text-[9px] uppercase tracking-widest px-2.5 py-1 rounded-sm border transition-all
                ${filterRole === role ? 'bg-blood-600/20 border-blood-500/40 text-blood-500 font-bold' : 'bg-zone-200 border-zone-400/20 text-zone-600 hover:text-zone-800'}`}
            >
              {DEV_ROLE_LABELS[role]} ({count})
            </button>
          );
        })}
      </div>

      {/* Dev cards */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-blood-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-zone-600">
          <AlertCircle className="w-8 h-8 mb-2" />
          <p className="font-mono text-xs uppercase tracking-widest">No developers found</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map((dev) => {
            const RoleIcon = DEV_ROLE_ICONS[dev.role_key] ?? Code2;
            const statusCfg = DEV_STATUS_CONFIG[dev.status];
            const isExpanded = expandedId === dev.id;

            return (
              <div
                key={dev.id}
                className={`bg-zone-panel border overflow-hidden clip-corner transition-all
                  ${isExpanded ? 'border-steel-500/30' : 'border-zone-400/20 hover:border-zone-500/40'}`}
              >
                {/* Top bar */}
                <div className="flex items-center gap-2 px-3 py-2 border-b border-zone-400/15 bg-zone-200/20">
                  <div className="w-7 h-7 bg-steel-700/30 flex items-center justify-center rounded-sm">
                    <RoleIcon className="w-3.5 h-3.5 text-steel-400" />
                  </div>
                  <span className="font-mono text-[8px] tracking-widest text-zone-600 uppercase">
                    {DEV_ROLE_LABELS[dev.role_key]}
                  </span>
                  <div className="ml-auto flex items-center gap-1.5">
                    <span className={statusCfg.dot} />
                    <span className={`font-mono text-[8px] uppercase tracking-widest ${statusCfg.color}`}>
                      {statusCfg.label}
                    </span>
                  </div>
                </div>

                {/* Body */}
                <div className="p-3">
                  <h3 className="font-display font-600 text-sm uppercase tracking-tight text-zone-900 mb-0.5">
                    {dev.codename}
                  </h3>
                  <p className="font-mono text-[9px] text-zone-600 tracking-wider mb-1">
                    {dev.designation}
                  </p>
                  <p className="text-[11px] text-zone-700 leading-snug mb-2">{dev.specialty}</p>

                  {/* Languages */}
                  {dev.languages.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2">
                      {dev.languages.map((lang) => (
                        <span key={lang} className="font-mono text-[8px] uppercase tracking-wider px-1.5 py-0.5 bg-steel-700/20 border border-steel-600/20 text-steel-400 rounded-sm">
                          {lang}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Frameworks */}
                  {dev.frameworks.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2">
                      {dev.frameworks.slice(0, 5).map((fw) => (
                        <span key={fw} className="font-mono text-[8px] uppercase tracking-wider px-1.5 py-0.5 bg-toxic-700/10 border border-toxic-600/20 text-toxic-400 rounded-sm">
                          {fw}
                        </span>
                      ))}
                      {dev.frameworks.length > 5 && (
                        <span className="font-mono text-[8px] text-zone-600">+{dev.frameworks.length - 5}</span>
                      )}
                    </div>
                  )}

                  {/* Git info */}
                  {(dev.git_branch || dev.current_project) && (
                    <div className="flex items-center gap-2 mb-2 pb-2 border-b border-zone-400/15">
                      {dev.git_branch && (
                        <div className="flex items-center gap-1">
                          <GitBranch className="w-3 h-3 text-amber-400" />
                          <span className="font-mono text-[9px] text-amber-400 tracking-wider">{dev.git_branch}</span>
                        </div>
                      )}
                      {dev.current_project && (
                        <span className="font-mono text-[9px] text-zone-600 ml-auto truncate" title={dev.current_project}>
                          {dev.current_project}
                        </span>
                      )}
                    </div>
                  )}

                  {/* Metrics */}
                  <div className="grid grid-cols-4 gap-1.5 mb-2">
                    <Metric label="CMT" value={dev.commits_today.toString()} color="text-toxic-400" />
                    <Metric label="PR" value={dev.prs_open.toString()} color="text-amber-400" />
                    <Metric label="QLT" value={`${dev.code_quality}`} color="text-steel-400" />
                    <Metric label="EFF" value={`${dev.efficiency}`} color="text-blood-500" />
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 pt-2 border-t border-zone-400/15">
                    <button
                      onClick={() => setExpandedId(isExpanded ? null : dev.id)}
                      className="font-mono text-[9px] uppercase tracking-widest text-zone-600 hover:text-zone-800 transition-colors"
                    >
                      {isExpanded ? 'Less' : 'More'}
                    </button>
                    <button
                      onClick={() => handleEdit(dev)}
                      className="flex items-center gap-1 font-mono text-[9px] uppercase tracking-widest text-zone-600 hover:text-amber-400 transition-colors ml-auto"
                    >
                      <Edit3 className="w-3 h-3" /> Edit
                    </button>
                    <button
                      onClick={() => handleDelete(dev.id)}
                      className="flex items-center gap-1 font-mono text-[9px] uppercase tracking-widest text-zone-600 hover:text-blood-500 transition-colors"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>

                  {/* Expanded */}
                  {isExpanded && (
                    <div className="mt-2 pt-2 border-t border-zone-400/15 animate-slide-up space-y-1.5">
                      {dev.frameworks.length > 5 && (
                        <div className="flex flex-wrap gap-1">
                          {dev.frameworks.slice(5).map((fw) => (
                            <span key={fw} className="font-mono text-[8px] uppercase tracking-wider px-1.5 py-0.5 bg-toxic-700/10 border border-toxic-600/20 text-toxic-400 rounded-sm">
                              {fw}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="font-mono text-[8px] text-zone-600/50">
                        Added: {new Date(dev.created_at).toLocaleDateString('en-GB')}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Add/Edit form modal */}
      {showForm && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-zone-100 border border-zone-400/30 clip-corner max-w-2xl w-full p-6 my-8" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Code2 className="w-5 h-5 text-steel-400" />
                <h3 className="font-display font-600 text-sm uppercase tracking-wide text-zone-900">
                  {editingId ? 'Edit Developer' : 'New Developer'}
                </h3>
              </div>
              <button onClick={handleCancel} className="text-zone-600 hover:text-zone-800"><X className="w-5 h-5" /></button>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Codename</label>
                <input type="text" value={formData.codename} onChange={(e) => setFormData((p) => ({ ...p, codename: e.target.value }))} placeholder="e.g. KOD-07"
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500" />
              </div>
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Role</label>
                <select value={formData.role_key} onChange={(e) => setFormData((p) => ({ ...p, role_key: e.target.value }))}
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500">
                  {ROLE_KEYS.map((r) => <option key={r} value={r}>{DEV_ROLE_LABELS[r]}</option>)}
                </select>
              </div>
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Designation (RU)</label>
                <input type="text" value={formData.designation} onChange={(e) => setFormData((p) => ({ ...p, designation: e.target.value }))} placeholder="e.g. Ведущий Разработчик"
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500" />
              </div>
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Status</label>
                <select value={formData.status} onChange={(e) => setFormData((p) => ({ ...p, status: e.target.value as DevAgentStatus }))}
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500">
                  <option value="active">Active</option>
                  <option value="coding">Coding</option>
                  <option value="reviewing">Reviewing</option>
                  <option value="idle">Standby</option>
                  <option value="offline">Offline</option>
                </select>
              </div>
            </div>

            <div className="mt-3">
              <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Specialty</label>
              <input type="text" value={formData.specialty} onChange={(e) => setFormData((p) => ({ ...p, specialty: e.target.value }))} placeholder="e.g. Frontend development, UI/UX"
                className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500" />
            </div>

            {/* Languages */}
            <div className="mt-3">
              <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Languages</label>
              <div className="flex flex-wrap gap-1.5">
                {ALL_LANGUAGES.map((lang) => (
                  <button
                    key={lang}
                    onClick={() => toggleLang(lang)}
                    className={`font-mono text-[9px] uppercase tracking-wider px-2 py-1 rounded-sm border transition-all
                      ${formData.languages.includes(lang)
                        ? 'bg-steel-700/30 border-steel-600/40 text-steel-400 font-bold'
                        : 'bg-zone-200 border-zone-400/20 text-zone-600 hover:text-zone-800'}`}
                  >
                    {lang}
                  </button>
                ))}
              </div>
            </div>

            {/* Frameworks */}
            <div className="mt-3">
              <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Frameworks & Tools</label>
              <div className="flex flex-wrap gap-1.5">
                {ALL_FRAMEWORKS.map((fw) => (
                  <button
                    key={fw}
                    onClick={() => toggleFramework(fw)}
                    className={`font-mono text-[9px] uppercase tracking-wider px-2 py-1 rounded-sm border transition-all
                      ${formData.frameworks.includes(fw)
                        ? 'bg-toxic-700/20 border-toxic-600/30 text-toxic-400 font-bold'
                        : 'bg-zone-200 border-zone-400/20 text-zone-600 hover:text-zone-800'}`}
                  >
                    {fw}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Commits</label>
                <input type="number" value={formData.commits_today} onChange={(e) => setFormData((p) => ({ ...p, commits_today: parseInt(e.target.value) || 0 }))}
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500" />
              </div>
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Open PRs</label>
                <input type="number" value={formData.prs_open} onChange={(e) => setFormData((p) => ({ ...p, prs_open: parseInt(e.target.value) || 0 }))}
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500" />
              </div>
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Quality</label>
                <input type="number" min="0" max="100" value={formData.code_quality} onChange={(e) => setFormData((p) => ({ ...p, code_quality: parseInt(e.target.value) || 0 }))}
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500" />
              </div>
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Efficiency</label>
                <input type="number" min="0" max="100" value={formData.efficiency} onChange={(e) => setFormData((p) => ({ ...p, efficiency: parseInt(e.target.value) || 0 }))}
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500" />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 mt-3">
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Current Project</label>
                <input type="text" value={formData.current_project} onChange={(e) => setFormData((p) => ({ ...p, current_project: e.target.value }))} placeholder="Project name"
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500" />
              </div>
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Git Branch</label>
                <input type="text" value={formData.git_branch} onChange={(e) => setFormData((p) => ({ ...p, git_branch: e.target.value }))} placeholder="feat/branch-name"
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500" />
              </div>
            </div>

            <div className="flex items-center gap-2 mt-5">
              <button
                onClick={handleSave}
                disabled={!formData.codename.trim() || saveStatus === 'saving'}
                className="flex items-center gap-1.5 bg-blood-600 text-zone-900 px-4 py-2 rounded-sm hover:bg-blood-500 transition-colors disabled:opacity-30 clip-corner-tl"
              >
                {saveStatus === 'saving' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                <span className="font-mono text-[10px] uppercase tracking-widest">
                  {editingId ? 'Update' : 'Add to Department'}
                </span>
              </button>
              {saveStatus === 'saved' && (
                <span className="flex items-center gap-1 text-toxic-400">
                  <CheckCircle2 className="w-4 h-4" />
                  <span className="font-mono text-[10px] uppercase tracking-widest">Saved</span>
                </span>
              )}
              {saveStatus === 'error' && (
                <span className="flex items-center gap-1 text-blood-500">
                  <AlertCircle className="w-4 h-4" />
                  <span className="font-mono text-[10px] uppercase tracking-widest">Error</span>
                </span>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ icon: Icon, label, value, color }: {
  icon: typeof Code2;
  label: string;
  value: string;
  color: string;
}) {
  return (
    <div className="bg-zone-panel border border-zone-400/20 clip-corner p-3 flex items-center gap-3">
      <div className="w-8 h-8 bg-zone-300/30 flex items-center justify-center rounded-sm">
        <Icon className={`w-4 h-4 ${color}`} />
      </div>
      <div>
        <div className="font-mono text-[8px] tracking-widest text-zone-600 uppercase">{label}</div>
        <div className={`font-display font-600 text-lg ${color}`}>{value}</div>
      </div>
    </div>
  );
}

function Metric({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-zone-200/30 border border-zone-400/15 rounded-sm px-1.5 py-1 text-center">
      <div className="font-mono text-[7px] text-zone-600 uppercase tracking-widest">{label}</div>
      <div className={`font-mono text-[11px] font-bold ${color}`}>{value}</div>
    </div>
  );
}
