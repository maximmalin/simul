import { useEffect, useState, useRef, useCallback } from 'react';
import {
  Workflow, Plus, X, Save, Loader2, Trash2, Play, Pause,
  ArrowRight, GitBranch, CheckCircle2, Clock, SkipForward,
  ScanLine, type LucideIcon,
} from 'lucide-react';
import { supabase } from '@/lib/supabase';
import type {
  Agent, Project, FlowWorkflow, FlowStep, FlowConnection,
  FlowWorkflowWithDetails, FlowStepWithAgent, StepType, StepStatus,
} from '@/lib/supabase';
import {
  ROLE_ICONS, WORKFLOW_STATUS_CONFIG, STEP_TYPE_CONFIG, STEP_STATUS_CONFIG,
} from '@/lib/constants';

const STEP_TYPES: StepType[] = ['input', 'process', 'decision', 'review', 'output'];

const STEP_TYPE_ICONS: Record<StepType, LucideIcon> = {
  input: GitBranch,
  process: Workflow,
  decision: GitBranch,
  review: ScanLine,
  output: CheckCircle2,
};

const STEP_STATUS_ICONS: Record<StepStatus, LucideIcon> = {
  pending: Clock,
  active: Loader2,
  done: CheckCircle2,
  skipped: SkipForward,
};

interface FlowEditorTabProps {
  agents: Agent[];
  projects: Project[];
}

export function FlowEditorTab({ agents, projects }: FlowEditorTabProps) {
  const [workflows, setWorkflows] = useState<FlowWorkflow[]>([]);
  const [selectedWorkflow, setSelectedWorkflow] = useState<FlowWorkflowWithDetails | null>(null);
  const [loading, setLoading] = useState(true);
  const [draggingStep, setDraggingStep] = useState<string | null>(null);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [selectedStep, setSelectedStep] = useState<FlowStep | null>(null);
  const [showStepForm, setShowStepForm] = useState(false);
  const [editingStep, setEditingStep] = useState<FlowStep | null>(null);
  const [connectingFrom, setConnectingFrom] = useState<string | null>(null);
  const [showWorkflowForm, setShowWorkflowForm] = useState(false);
  const [newWorkflowName, setNewWorkflowName] = useState('');
  const [newWorkflowDesc, setNewWorkflowDesc] = useState('');
  const [newWorkflowProject, setNewWorkflowProject] = useState('');
  const canvasRef = useRef<HTMLDivElement>(null);

  const [stepForm, setStepForm] = useState({
    title: '',
    directive: '',
    step_type: 'process' as StepType,
    agent_id: '',
  });

  const loadWorkflow = useCallback(async (id: string) => {
    const { data: wf } = await supabase
      .from('flow_workflows')
      .select(`
        *,
        project:bureau_projects(id, code, title)
      `)
      .eq('id', id)
      .maybeSingle();

    if (!wf) return;

    const { data: steps } = await supabase
      .from('flow_steps')
      .select(`
        *,
        agent:bureau_agents!agent_id(id, codename, role_key, role_title, zone_callsign)
      `)
      .eq('workflow_id', id)
      .order('sort_order');

    const { data: conns } = await supabase
      .from('flow_connections')
      .select('*')
      .eq('workflow_id', id);

    setSelectedWorkflow({
      ...(wf as unknown as FlowWorkflow),
      project: (wf as Record<string, unknown>).project as FlowWorkflowWithDetails['project'],
      steps: (steps ?? []) as unknown as FlowWorkflowWithDetails['steps'],
      connections: (conns ?? []) as FlowConnection[],
    });
  }, []);

  const fetchWorkflows = useCallback(async () => {
    const { data } = await supabase
      .from('flow_workflows')
      .select('*')
      .order('created_at', { ascending: false });
    setWorkflows((data ?? []) as FlowWorkflow[]);
    if (data && data.length > 0 && !selectedWorkflow) {
      await loadWorkflow(data[0].id);
    }
    setLoading(false);
  }, [selectedWorkflow, loadWorkflow]);


  useEffect(() => {
    fetchWorkflows();
  }, [fetchWorkflows]);

  const handleMouseDown = (e: React.MouseEvent, step: FlowStep) => {
    if (connectingFrom) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    setDraggingStep(step.id);
    setDragOffset({
      x: e.clientX - rect.left - step.position_x,
      y: e.clientY - rect.top - step.position_y,
    });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!draggingStep || !canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    const x = Math.max(0, e.clientX - rect.left - dragOffset.x);
    const y = Math.max(0, e.clientY - rect.top - dragOffset.y);
    setDraggingStep(draggingStep);

    setSelectedWorkflow((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        steps: prev.steps?.map((s) =>
          s.id === draggingStep ? { ...s, position_x: Math.round(x), position_y: Math.round(y) } : s
        ),
      };
    });
  };

  const handleMouseUp = useCallback(async () => {
    if (!draggingStep || !selectedWorkflow) {
      setDraggingStep(null);
      return;
    }
    const step = selectedWorkflow.steps?.find((s) => s.id === draggingStep);
    if (step) {
      await supabase
        .from('flow_steps')
        .update({ position_x: step.position_x, position_y: step.position_y })
        .eq('id', step.id);
    }
    setDraggingStep(null);
  }, [draggingStep, selectedWorkflow]);

  useEffect(() => {
    window.addEventListener('mouseup', handleMouseUp);
    return () => window.removeEventListener('mouseup', handleMouseUp);
  }, [handleMouseUp]);

  const handleCreateWorkflow = async () => {
    if (!newWorkflowName.trim()) return;
    const { data } = await supabase
      .from('flow_workflows')
      .insert({
        name: newWorkflowName.trim(),
        description: newWorkflowDesc.trim(),
        project_id: newWorkflowProject || null,
        status: 'draft',
      })
      .select('*')
      .single();
    if (data) {
      setWorkflows((prev) => [data as FlowWorkflow, ...prev]);
      await loadWorkflow((data as FlowWorkflow).id);
      setShowWorkflowForm(false);
      setNewWorkflowName('');
      setNewWorkflowDesc('');
      setNewWorkflowProject('');
    }
  };

  const handleCreateStep = async () => {
    if (!selectedWorkflow || !stepForm.title.trim()) return;
    const maxOrder = selectedWorkflow.steps?.reduce((max, s) => Math.max(max, s.sort_order), -1) ?? -1;
    const offset = (selectedWorkflow.steps?.length ?? 0) * 260;

    const { data } = await supabase
      .from('flow_steps')
      .insert({
        workflow_id: selectedWorkflow.id,
        agent_id: stepForm.agent_id || null,
        title: stepForm.title.trim(),
        directive: stepForm.directive.trim(),
        step_type: stepForm.step_type,
        position_x: 80 + offset % 780,
        position_y: 120 + Math.floor(offset / 780) * 200,
        sort_order: maxOrder + 1,
        status: 'pending',
      })
      .select(`
        *,
        agent:bureau_agents!agent_id(id, codename, role_key, role_title, zone_callsign)
      `)
      .single();

    if (data) {
      setSelectedWorkflow((prev) => prev ? {
        ...prev,
        steps: [...(prev.steps ?? []), data as unknown as FlowStepWithAgent],
      } : prev);
      setShowStepForm(false);
      setStepForm({ title: '', directive: '', step_type: 'process', agent_id: '' });
    }
  };

  const handleUpdateStep = async () => {
    if (!editingStep || !selectedWorkflow) return;
    const { data } = await supabase
      .from('flow_steps')
      .update({
        title: stepForm.title,
        directive: stepForm.directive,
        step_type: stepForm.step_type,
        agent_id: stepForm.agent_id || null,
      })
      .eq('id', editingStep.id)
      .select(`
        *,
        agent:bureau_agents!agent_id(id, codename, role_key, role_title, zone_callsign)
      `)
      .single();

    if (data) {
      setSelectedWorkflow((prev) => prev ? {
        ...prev,
        steps: prev.steps?.map((s) => s.id === editingStep.id ? data as unknown as FlowStepWithAgent : s) ?? [],
      } : prev);
      setEditingStep(null);
      setShowStepForm(false);
      setStepForm({ title: '', directive: '', step_type: 'process', agent_id: '' });
    }
  };

  const handleDeleteStep = async (stepId: string) => {
    await supabase.from('flow_connections').delete().or(`from_step_id.eq.${stepId},to_step_id.eq.${stepId}`);
    await supabase.from('flow_steps').delete().eq('id', stepId);
    setSelectedWorkflow((prev) => prev ? {
      ...prev,
      steps: prev.steps?.filter((s) => s.id !== stepId) ?? [],
      connections: prev.connections?.filter((c) => c.from_step_id !== stepId && c.to_step_id !== stepId) ?? [],
    } : prev);
    setSelectedStep(null);
  };

  const handleConnect = async (fromId: string, toId: string) => {
    if (fromId === toId || !selectedWorkflow) return;
    const exists = selectedWorkflow.connections?.some(
      (c) => c.from_step_id === fromId && c.to_step_id === toId
    );
    if (exists) return;

    const { data } = await supabase
      .from('flow_connections')
      .insert({
        workflow_id: selectedWorkflow.id,
        from_step_id: fromId,
        to_step_id: toId,
      })
      .select('*')
      .single();

    if (data) {
      setSelectedWorkflow((prev) => prev ? {
        ...prev,
        connections: [...(prev.connections ?? []), data as FlowConnection],
      } : prev);
    }
  };

  const handleStepClick = (stepId: string) => {
    if (connectingFrom) {
      handleConnect(connectingFrom, stepId);
      setConnectingFrom(null);
    } else {
      const step = selectedWorkflow?.steps?.find((s) => s.id === stepId) ?? null;
      setSelectedStep(step);
    }
  };

  const handleCycleStepStatus = async (step: FlowStep) => {
    const order: StepStatus[] = ['pending', 'active', 'done', 'skipped'];
    const next = order[(order.indexOf(step.status) + 1) % order.length];
    await supabase.from('flow_steps').update({ status: next }).eq('id', step.id);
    setSelectedWorkflow((prev) => prev ? {
      ...prev,
      steps: prev.steps?.map((s) => s.id === step.id ? { ...s, status: next } : s) ?? [],
    } : prev);
  };

  const handleEditStep = (step: FlowStep) => {
    setEditingStep(step);
    setStepForm({
      title: step.title,
      directive: step.directive,
      step_type: step.step_type,
      agent_id: step.agent_id ?? '',
    });
    setShowStepForm(true);
    setSelectedStep(null);
  };

  const handleDeleteConnection = async (connId: string) => {
    await supabase.from('flow_connections').delete().eq('id', connId);
    setSelectedWorkflow((prev) => prev ? {
      ...prev,
      connections: prev.connections?.filter((c) => c.id !== connId) ?? [],
    } : prev);
  };

  const handleUpdateWorkflowStatus = async (status: FlowWorkflow['status']) => {
    if (!selectedWorkflow) return;
    await supabase.from('flow_workflows').update({ status, updated_at: new Date().toISOString() }).eq('id', selectedWorkflow.id);
    setSelectedWorkflow((prev) => prev ? { ...prev, status } : prev);
    setWorkflows((prev) => prev.map((w) => w.id === selectedWorkflow.id ? { ...w, status } : w));
  };

  const renderConnection = (conn: FlowConnection) => {
    const fromStep = selectedWorkflow?.steps?.find((s) => s.id === conn.from_step_id);
    const toStep = selectedWorkflow?.steps?.find((s) => s.id === conn.to_step_id);
    if (!fromStep || !toStep) return null;

    const fromX = fromStep.position_x + 140;
    const fromY = fromStep.position_y + 36;
    const toX = toStep.position_x;
    const toY = toStep.position_y + 36;

    const midX = (fromX + toX) / 2;
    const path = `M ${fromX} ${fromY} C ${midX} ${fromY}, ${midX} ${toY}, ${toX} ${toY}`;

    return (
      <g key={conn.id} className="cursor-pointer" onClick={() => handleDeleteConnection(conn.id)}>
        <path
          d={path}
          fill="none"
          stroke="#6b7280"
          strokeWidth="2"
          strokeDasharray="4 2"
          className="hover:stroke-blood-500 transition-colors"
        />
        <circle cx={toX} cy={toY} r="4" fill="#6b7280" className="hover:fill-blood-500" />
      </g>
    );
  };

  const renderStep = (step: FlowStepWithAgent) => {
    const Icon = step.agent?.role_key ? ROLE_ICONS[step.agent.role_key] ?? Workflow : Workflow;
    const StepIcon = STEP_TYPE_ICONS[step.step_type as StepType];
    const StatusIcon = STEP_STATUS_ICONS[step.status as StepStatus];
    const typeCfg = STEP_TYPE_CONFIG[step.step_type as StepType];
    const statusCfg = STEP_STATUS_CONFIG[step.status as StepStatus];
    const isDragging = draggingStep === step.id;
    const isSelected = selectedStep?.id === step.id;
    const isConnecting = connectingFrom === step.id;

    return (
      <div
        key={step.id}
        onMouseDown={(e) => handleMouseDown(e, step)}
        onClick={(e) => { e.stopPropagation(); handleStepClick(step.id); }}
        className={`absolute w-[140px] select-none cursor-grab transition-shadow
          ${isDragging ? 'cursor-grabbing shadow-2xl z-50' : 'z-10'}
          ${isSelected ? 'ring-2 ring-blood-500' : ''}
          ${isConnecting ? 'ring-2 ring-toxic-400' : ''}`}
        style={{ left: step.position_x, top: step.position_y }}
      >
        <div className={`border ${typeCfg.border} ${typeCfg.bg} rounded-sm overflow-hidden`}>
          {/* Header */}
          <div className="flex items-center gap-1.5 px-2 py-1.5 border-b border-zone-400/15">
            <div className="w-5 h-5 bg-zone-300/40 flex items-center justify-center rounded-sm">
              <StepIcon className="w-3 h-3 text-zone-700" />
            </div>
            <span className={`font-mono text-[8px] uppercase tracking-widest ${typeCfg.color}`}>
              {typeCfg.label}
            </span>
            <button
              onClick={(e) => { e.stopPropagation(); handleCycleStepStatus(step); }}
              className="ml-auto"
            >
              <StatusIcon className={`w-3 h-3 ${statusCfg.color} ${step.status === 'active' ? 'animate-spin' : ''}`} />
            </button>
          </div>
          {/* Body */}
          <div className="px-2 py-1.5">
            <p className="font-display text-[11px] font-600 uppercase tracking-tight text-zone-900 leading-tight mb-1">
              {step.title}
            </p>
            {step.agent && (
              <div className="flex items-center gap-1">
                <Icon className="w-2.5 h-2.5 text-zone-600" />
                <span className="font-mono text-[8px] text-zone-600 tracking-wider">
                  {step.agent.codename}
                </span>
              </div>
            )}
          </div>
          {/* Connect handle */}
          <button
            onClick={(e) => {
              e.stopPropagation();
              setConnectingFrom(connectingFrom === step.id ? null : step.id);
            }}
            className={`w-full flex items-center justify-center gap-1 py-0.5 border-t border-zone-400/15 transition-colors
              ${isConnecting ? 'bg-toxic-500/20' : 'hover:bg-zone-300/20'}`}
          >
            <ArrowRight className={`w-2.5 h-2.5 ${isConnecting ? 'text-toxic-400' : 'text-zone-600'}`} />
            <span className="font-mono text-[7px] uppercase tracking-widest text-zone-600">
              {isConnecting ? 'Linking...' : 'Connect'}
            </span>
          </button>
        </div>
      </div>
    );
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="w-8 h-8 border-4 border-blood-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="px-4 md:px-6 py-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-5">
        <div className="w-8 h-8 bg-steel-600 flex items-center justify-center clip-corner-tl">
          <Workflow className="w-4 h-4 text-zone-900" />
        </div>
        <div>
          <h2 className="font-display font-600 text-xl uppercase tracking-tight text-zone-900">
            Flow Editor
          </h2>
          <p className="font-mono text-[10px] tracking-widest text-zone-600 uppercase">
            Visual Agent Task Workflows · Drag · Connect · Execute
          </p>
        </div>
        <button
          onClick={() => setShowWorkflowForm(true)}
          className="ml-auto flex items-center gap-1.5 bg-blood-600/20 border border-blood-500/30 hover:bg-blood-600/30 transition-all px-3 py-2 rounded-sm clip-corner-tl"
        >
          <Plus className="w-4 h-4 text-blood-500" />
          <span className="font-mono text-[10px] uppercase tracking-widest text-blood-500">New Flow</span>
        </button>
      </div>

      {/* Workflow selector */}
      <div className="flex flex-wrap gap-1.5 mb-4">
        {workflows.map((wf) => {
          const cfg = WORKFLOW_STATUS_CONFIG[wf.status];
          const isActive = selectedWorkflow?.id === wf.id;
          return (
            <button
              key={wf.id}
              onClick={() => loadWorkflow(wf.id)}
              className={`flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest px-3 py-1.5 rounded-sm border transition-all
                ${isActive ? 'bg-steel-700/30 border-steel-600 text-steel-400 font-bold' : 'bg-zone-200 border-zone-400/20 text-zone-600 hover:text-zone-800'}`}
            >
              {wf.name}
              <span className={`px-1.5 py-0.5 rounded-sm ${cfg.bg} ${cfg.border} border ${cfg.color} text-[8px]`}>
                {cfg.label}
              </span>
            </button>
          );
        })}
      </div>

      {selectedWorkflow && (
        <>
          {/* Workflow info bar */}
          <div className="flex items-center gap-3 mb-3 px-3 py-2 bg-zone-panel border border-zone-400/20 rounded-sm">
            <div>
              <h3 className="font-display font-600 text-sm uppercase tracking-tight text-zone-900">
                {selectedWorkflow.name}
              </h3>
              {selectedWorkflow.description && (
                <p className="text-[11px] text-zone-600 mt-0.5">{selectedWorkflow.description}</p>
              )}
            </div>
            <div className="ml-auto flex items-center gap-2">
              {selectedWorkflow.project && (
                <span className="font-mono text-[9px] text-blood-500/80 tracking-wider px-2 py-0.5 bg-blood-900/10 border border-blood-700/20 rounded-sm">
                  {selectedWorkflow.project.code}
                </span>
              )}
              <button
                onClick={() => handleUpdateWorkflowStatus(selectedWorkflow.status === 'active' ? 'paused' : 'active')}
                className={`flex items-center gap-1 px-2 py-1 rounded-sm border transition-all
                  ${selectedWorkflow.status === 'active'
                    ? 'bg-amber-600/15 border-amber-500/30 text-amber-400'
                    : 'bg-toxic-700/15 border-toxic-600/30 text-toxic-400'}`}
              >
                {selectedWorkflow.status === 'active' ? <Pause className="w-3 h-3" /> : <Play className="w-3 h-3" />}
                <span className="font-mono text-[9px] uppercase tracking-widest">
                  {selectedWorkflow.status === 'active' ? 'Pause' : 'Activate'}
                </span>
              </button>
              <button
                onClick={() => { setEditingStep(null); setStepForm({ title: '', directive: '', step_type: 'process', agent_id: '' }); setShowStepForm(true); }}
                className="flex items-center gap-1 px-2 py-1 rounded-sm border bg-steel-700/15 border-steel-600/30 text-steel-400 hover:bg-steel-700/25 transition-all"
              >
                <Plus className="w-3 h-3" />
                <span className="font-mono text-[9px] uppercase tracking-widest">Step</span>
              </button>
            </div>
          </div>

          {/* Canvas */}
          <div
            ref={canvasRef}
            onMouseMove={handleMouseMove}
            onClick={() => { setSelectedStep(null); setConnectingFrom(null); }}
            className="relative w-full h-[500px] bg-zone-100/30 border border-zone-400/20 rounded-sm overflow-hidden crt-grid"
            style={{ minHeight: '500px' }}
          >
            {/* SVG connections layer */}
            <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ zIndex: 5 }}>
              {selectedWorkflow.connections?.map(renderConnection)}
            </svg>

            {/* Steps */}
            {selectedWorkflow.steps?.map((step) => renderStep(step))}

            {/* Empty state */}
            {(!selectedWorkflow.steps || selectedWorkflow.steps.length === 0) && (
              <div className="absolute inset-0 flex flex-col items-center justify-center text-zone-600/40">
                <Workflow className="w-8 h-8 mb-2" />
                <p className="font-mono text-xs uppercase tracking-widest">No steps yet</p>
                <p className="font-mono text-[10px] mt-1">Click "Step" to add the first node</p>
              </div>
            )}

            {/* Connecting hint */}
            {connectingFrom && (
              <div className="absolute top-2 left-1/2 -translate-x-1/2 bg-toxic-600/20 border border-toxic-500/30 px-3 py-1.5 rounded-sm z-50">
                <span className="font-mono text-[10px] uppercase tracking-widest text-toxic-400">
                  Click a target step to connect...
                </span>
              </div>
            )}
          </div>

          {/* Step count + connection count */}
          <div className="flex items-center gap-4 mt-2 font-mono text-[9px] text-zone-600 uppercase tracking-widest">
            <span>{selectedWorkflow.steps?.length ?? 0} Steps</span>
            <span>{selectedWorkflow.connections?.length ?? 0} Connections</span>
            <span>{selectedWorkflow.steps?.filter((s) => s.status === 'done').length ?? 0} Done</span>
          </div>
        </>
      )}

      {/* New workflow modal */}
      {showWorkflowForm && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={() => setShowWorkflowForm(false)}>
          <div className="bg-zone-100 border border-zone-400/30 clip-corner max-w-md w-full p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-display font-600 text-sm uppercase tracking-wide text-zone-900">New Workflow</h3>
              <button onClick={() => setShowWorkflowForm(false)} className="text-zone-600 hover:text-zone-800"><X className="w-5 h-5" /></button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Name</label>
                <input type="text" value={newWorkflowName} onChange={(e) => setNewWorkflowName(e.target.value)} placeholder="Workflow name..."
                  className="w-full text-sm bg-zone-200 border border-zone-400/20 rounded-sm px-3 py-2 text-zone-900 placeholder-zone-600 focus:outline-none focus:border-blood-500" />
              </div>
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Description</label>
                <textarea value={newWorkflowDesc} onChange={(e) => setNewWorkflowDesc(e.target.value)} rows={2} placeholder="What does this workflow accomplish?"
                  className="w-full text-sm bg-zone-200 border border-zone-400/20 rounded-sm px-3 py-2 text-zone-900 placeholder-zone-600 focus:outline-none focus:border-blood-500 resize-none" />
              </div>
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Project (optional)</label>
                <select value={newWorkflowProject} onChange={(e) => setNewWorkflowProject(e.target.value)}
                  className="w-full text-sm bg-zone-200 border border-zone-400/20 rounded-sm px-3 py-2 text-zone-900 focus:outline-none focus:border-blood-500">
                  <option value="">No project</option>
                  {projects.map((p) => <option key={p.id} value={p.id}>{p.code} — {p.title}</option>)}
                </select>
              </div>
              <button onClick={handleCreateWorkflow} disabled={!newWorkflowName.trim()}
                className="w-full flex items-center justify-center gap-2 bg-blood-600 text-zone-900 px-4 py-2.5 rounded-sm hover:bg-blood-500 transition-colors disabled:opacity-30 clip-corner-tl">
                <Save className="w-4 h-4" />
                <span className="font-mono text-[10px] uppercase tracking-widest">Create Workflow</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Step form modal */}
      {showStepForm && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={() => { setShowStepForm(false); setEditingStep(null); }}>
          <div className="bg-zone-100 border border-zone-400/30 clip-corner max-w-md w-full p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-display font-600 text-sm uppercase tracking-wide text-zone-900">
                {editingStep ? 'Edit Step' : 'New Step'}
              </h3>
              <button onClick={() => { setShowStepForm(false); setEditingStep(null); }} className="text-zone-600 hover:text-zone-800"><X className="w-5 h-5" /></button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Title</label>
                <input type="text" value={stepForm.title} onChange={(e) => setStepForm((p) => ({ ...p, title: e.target.value }))} placeholder="Step name..."
                  className="w-full text-sm bg-zone-200 border border-zone-400/20 rounded-sm px-3 py-2 text-zone-900 placeholder-zone-600 focus:outline-none focus:border-blood-500" />
              </div>
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Directive</label>
                <textarea value={stepForm.directive} onChange={(e) => setStepForm((p) => ({ ...p, directive: e.target.value }))} rows={2} placeholder="What should the agent do?"
                  className="w-full text-sm bg-zone-200 border border-zone-400/20 rounded-sm px-3 py-2 text-zone-900 placeholder-zone-600 focus:outline-none focus:border-blood-500 resize-none" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Type</label>
                  <select value={stepForm.step_type} onChange={(e) => setStepForm((p) => ({ ...p, step_type: e.target.value as StepType }))}
                    className="w-full text-sm bg-zone-200 border border-zone-400/20 rounded-sm px-3 py-2 text-zone-900 focus:outline-none focus:border-blood-500">
                    {STEP_TYPES.map((t) => <option key={t} value={t}>{STEP_TYPE_CONFIG[t].label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Agent</label>
                  <select value={stepForm.agent_id} onChange={(e) => setStepForm((p) => ({ ...p, agent_id: e.target.value }))}
                    className="w-full text-sm bg-zone-200 border border-zone-400/20 rounded-sm px-3 py-2 text-zone-900 focus:outline-none focus:border-blood-500">
                    <option value="">Unassigned</option>
                    {agents.map((a) => <option key={a.id} value={a.id}>{a.codename} — {a.role_title}</option>)}
                  </select>
                </div>
              </div>
              <button onClick={editingStep ? handleUpdateStep : handleCreateStep} disabled={!stepForm.title.trim()}
                className="w-full flex items-center justify-center gap-2 bg-blood-600 text-zone-900 px-4 py-2.5 rounded-sm hover:bg-blood-500 transition-colors disabled:opacity-30 clip-corner-tl">
                <Save className="w-4 h-4" />
                <span className="font-mono text-[10px] uppercase tracking-widest">{editingStep ? 'Update Step' : 'Add Step'}</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Step detail panel */}
      {selectedStep && (
        <div className="fixed bottom-4 right-4 z-40 bg-zone-100 border border-zone-400/30 clip-corner max-w-xs w-full p-4 shadow-xl">
          <div className="flex items-center justify-between mb-2">
            <h4 className="font-display font-600 text-sm uppercase tracking-tight text-zone-900">{selectedStep.title}</h4>
            <button onClick={() => setSelectedStep(null)} className="text-zone-600 hover:text-zone-800"><X className="w-4 h-4" /></button>
          </div>
          {selectedStep.directive && <p className="text-[11px] text-zone-600 mb-2">{selectedStep.directive}</p>}
          <div className="flex items-center gap-2 mb-3">
            <span className={`px-2 py-0.5 text-[8px] font-mono uppercase tracking-widest rounded-sm border ${STEP_TYPE_CONFIG[selectedStep.step_type].bg} ${STEP_TYPE_CONFIG[selectedStep.step_type].color} ${STEP_TYPE_CONFIG[selectedStep.step_type].border}`}>
              {STEP_TYPE_CONFIG[selectedStep.step_type].label}
            </span>
            <span className={`flex items-center gap-1 font-mono text-[9px] uppercase tracking-widest ${STEP_STATUS_CONFIG[selectedStep.status].color}`}>
              <span className={STEP_STATUS_CONFIG[selectedStep.status].dot} />
              {STEP_STATUS_CONFIG[selectedStep.status].label}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={() => handleEditStep(selectedStep)} className="flex items-center gap-1 font-mono text-[9px] uppercase tracking-widest text-amber-400 hover:text-amber-300 transition-colors">
              <Workflow className="w-3 h-3" /> Edit
            </button>
            <button onClick={() => handleDeleteStep(selectedStep.id)} className="flex items-center gap-1 font-mono text-[9px] uppercase tracking-widest text-blood-500 hover:text-blood-400 transition-colors ml-auto">
              <Trash2 className="w-3 h-3" /> Delete
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
