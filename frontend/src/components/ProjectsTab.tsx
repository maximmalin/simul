import { useState } from 'react';
import { FolderKanban, Plus, X, AlertCircle, ChevronDown } from 'lucide-react';
import type { Project, Task, Agent, TaskStatus, Priority } from '@/lib/supabase';
import {
  PROJECT_STATUS_CONFIG,
  TASK_STATUS_CONFIG,
  PRIORITY_CONFIG,
  TASK_STATUSES,
} from '@/lib/constants';

interface ProjectsTabProps {
  projects: Project[];
  tasks: Task[];
  agents: Agent[];
  selectedAgentId: string | null;
  onCreateTask: (task: {
    project_id: string;
    agent_id: string | null;
    title: string;
    directive: string;
    priority: Priority;
  }) => void;
  onUpdateTaskStatus: (taskId: string, status: TaskStatus) => void;
  onReassignTask: (taskId: string, agentId: string | null) => void;
}

export function ProjectsTab({
  projects,
  tasks,
  agents,
  selectedAgentId,
  onCreateTask,
  onUpdateTaskStatus,
  onReassignTask,
}: ProjectsTabProps) {
  const [expandedProject, setExpandedProject] = useState<string | null>(null);
  const [showNewTaskForm, setShowNewTaskForm] = useState<string | null>(null);
  const [newTaskTitle, setNewTaskTitle] = useState('');
  const [newTaskDirective, setNewTaskDirective] = useState('');
  const [newTaskPriority, setNewTaskPriority] = useState<Priority>('standard');
  const [newTaskAgent, setNewTaskAgent] = useState<string>('');

  const projectTasks = (projectId: string) =>
    tasks.filter((t) => t.project_id === projectId).sort((a, b) => a.sort_order - b.sort_order);

  const agentById = (id: string | null) => id ? agents.find((a) => a.id === id) : null;

  const handleCreate = (projectId: string) => {
    if (!newTaskTitle.trim()) return;
    onCreateTask({
      project_id: projectId,
      agent_id: newTaskAgent || null,
      title: newTaskTitle.trim(),
      directive: newTaskDirective.trim(),
      priority: newTaskPriority,
    });
    setNewTaskTitle('');
    setNewTaskDirective('');
    setNewTaskPriority('standard');
    setNewTaskAgent('');
    setShowNewTaskForm(null);
  };

  return (
    <div className="px-4 md:px-6 py-6 max-w-7xl mx-auto">
      <div className="flex items-center gap-3 mb-5">
        <div className="w-8 h-8 bg-rust-600 flex items-center justify-center clip-corner-tl">
          <FolderKanban className="w-4 h-4 text-zone-900" />
        </div>
        <div>
          <h2 className="font-display font-600 text-xl uppercase tracking-tight text-zone-900">
            Project Directorate
          </h2>
          <p className="font-mono text-[10px] tracking-widest text-zone-600 uppercase">
            Engineering Programs · {projects.length} Active
          </p>
        </div>
      </div>

      <div className="space-y-3">
        {projects.map((project, pIdx) => {
          const pTasks = projectTasks(project.id);
          const pStatus = PROJECT_STATUS_CONFIG[project.status];
          const isExpanded = expandedProject === project.id;

          return (
            <div
              key={project.id}
              className="bg-zone-panel border border-zone-400/20 clip-corner overflow-hidden"
            >
              {/* Project header */}
              <div
                className="flex items-center justify-between px-5 py-4 cursor-pointer hover:bg-zone-200/30 transition-colors"
                onClick={() => setExpandedProject(isExpanded ? null : project.id)}
              >
                <div className="flex items-center gap-4">
                  <div className="font-display text-3xl font-700 text-zone-400/10 select-none">
                    {String(pIdx + 1).padStart(2, '0')}
                  </div>
                  <div>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-mono text-[10px] tracking-widest text-blood-500 uppercase font-bold">
                        {project.code}
                      </span>
                      <span className={`px-2 py-0.5 text-[9px] font-mono uppercase tracking-widest rounded-sm border ${pStatus.bg} ${pStatus.color} ${pStatus.border}`}>
                        {pStatus.label}
                      </span>
                      <span className={`px-2 py-0.5 text-[9px] font-mono uppercase tracking-widest rounded-sm ${PRIORITY_CONFIG[project.priority].bg} ${PRIORITY_CONFIG[project.priority].color}`}>
                        {PRIORITY_CONFIG[project.priority].label}
                      </span>
                    </div>
                    <h3 className="font-display font-600 text-lg uppercase tracking-tight text-zone-900">
                      {project.title}
                    </h3>
                    <p className="text-xs text-zone-600 mt-0.5 max-w-2xl">
                      {project.objective}
                    </p>
                  </div>
                </div>

                <div className="flex items-center gap-4">
                  <div className="hidden md:flex flex-col items-end gap-1">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[10px] text-zone-600 uppercase tracking-wider">Progress</span>
                      <span className="font-mono text-sm font-bold text-zone-900 tabular-nums">{project.progress}%</span>
                    </div>
                    <div className="w-32 h-2 progress-track rounded-sm overflow-hidden">
                      <div className="h-full bg-blood-500" style={{ width: `${project.progress}%` }} />
                    </div>
                  </div>
                  <ChevronDown
                    className={`w-5 h-5 text-zone-600 transition-transform duration-200
                      ${isExpanded ? 'rotate-180' : ''}`}
                  />
                </div>
              </div>

              {/* Expanded task board */}
              {isExpanded && (
                <div className="border-t border-zone-400/15 bg-zone-100/50 animate-slide-up">
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3 p-4">
                    {TASK_STATUSES.map((status) => {
                      const colTasks = pTasks.filter((t) => t.status === status);
                      const cfg = TASK_STATUS_CONFIG[status];

                      return (
                        <div key={status} className="bg-zone-200/30 border border-zone-400/15 rounded-sm">
                          <div className={`px-3 py-2 border-b ${cfg.border} flex items-center justify-between`}>
                            <span className={`font-mono text-[10px] tracking-widest uppercase font-bold ${cfg.color}`}>
                              {cfg.label}
                            </span>
                            <span className={`font-mono text-[10px] ${cfg.color} tabular-nums`}>
                              {colTasks.length}
                            </span>
                          </div>

                          <div className="p-2 space-y-2 min-h-[60px]">
                            {colTasks.map((task) => {
                              const agent = agentById(task.agent_id);
                              const isAssignedToSelected = selectedAgentId && task.agent_id === selectedAgentId;

                              return (
                                <div
                                  key={task.id}
                                  className={`bg-zone-100 border rounded-sm p-2.5 transition-all
                                    ${isAssignedToSelected
                                      ? 'border-blood-500/40 shadow-sm shadow-blood-900/10'
                                      : 'border-zone-400/15 hover:border-zone-500/30'}`}
                                >
                                  <div className="flex items-start justify-between gap-2 mb-1.5">
                                    <p className="text-xs font-bold text-zone-900 leading-snug">
                                      {task.title}
                                    </p>
                                    <span className={`px-1.5 py-0.5 text-[8px] font-mono uppercase tracking-wider rounded-sm flex-shrink-0
                                      ${PRIORITY_CONFIG[task.priority].bg} ${PRIORITY_CONFIG[task.priority].color}`}>
                                      {PRIORITY_CONFIG[task.priority].label}
                                    </span>
                                  </div>

                                  {task.directive && (
                                    <p className="text-[10px] text-zone-600 leading-snug mb-2">
                                      {task.directive}
                                    </p>
                                  )}

                                  <div className="flex items-center gap-1.5 mb-2">
                                    <select
                                      value={task.agent_id ?? ''}
                                      onChange={(e) => onReassignTask(task.id, e.target.value || null)}
                                      className="text-[10px] font-mono bg-zone-200 border border-zone-400/20 rounded-sm px-1.5 py-1 text-zone-800 focus:outline-none focus:border-blood-500 cursor-pointer"
                                    >
                                      <option value="">Unassigned</option>
                                      {agents.map((a) => (
                                        <option key={a.id} value={a.id}>{a.codename}</option>
                                      ))}
                                    </select>
                                    {agent && (
                                      <span className="text-[9px] font-mono text-zone-600">
                                        {agent.zone_callsign}
                                      </span>
                                    )}
                                  </div>

                                  <div className="flex items-center gap-1 flex-wrap">
                                    {TASK_STATUSES.map((s) => (
                                      <button
                                        key={s}
                                        onClick={() => onUpdateTaskStatus(task.id, s)}
                                        className={`text-[8px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm transition-all
                                          ${task.status === s
                                            ? `${TASK_STATUS_CONFIG[s].bg} ${TASK_STATUS_CONFIG[s].color} border ${TASK_STATUS_CONFIG[s].border} font-bold`
                                            : 'bg-transparent text-zone-600 hover:bg-zone-300/30'}`}
                                      >
                                        {TASK_STATUS_CONFIG[s].label}
                                      </button>
                                    ))}
                                  </div>
                                </div>
                              );
                            })}
                            {colTasks.length === 0 && (
                              <div className="flex items-center justify-center h-12 text-[10px] font-mono text-zone-600/40 uppercase tracking-wider">
                                Empty
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  {/* New task form */}
                  <div className="px-4 pb-4">
                    {showNewTaskForm === project.id ? (
                      <div className="bg-zone-200/40 border border-zone-400/20 rounded-sm p-4 space-y-3">
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-[10px] tracking-widest text-zone-800 uppercase font-bold">
                            New Directive
                          </span>
                          <button onClick={() => setShowNewTaskForm(null)} className="text-zone-600 hover:text-zone-800">
                            <X className="w-4 h-4" />
                          </button>
                        </div>
                        <input
                          type="text"
                          placeholder="Task title..."
                          value={newTaskTitle}
                          onChange={(e) => setNewTaskTitle(e.target.value)}
                          className="w-full text-sm bg-zone-100 border border-zone-400/20 rounded-sm px-3 py-2 text-zone-900 placeholder-zone-600 focus:outline-none focus:border-blood-500"
                        />
                        <textarea
                          placeholder="Directive details..."
                          value={newTaskDirective}
                          onChange={(e) => setNewTaskDirective(e.target.value)}
                          rows={2}
                          className="w-full text-xs bg-zone-100 border border-zone-400/20 rounded-sm px-3 py-2 text-zone-900 placeholder-zone-600 focus:outline-none focus:border-blood-500 resize-none"
                        />
                        <div className="flex flex-wrap items-center gap-3">
                          <select
                            value={newTaskAgent}
                            onChange={(e) => setNewTaskAgent(e.target.value)}
                            className="text-xs font-mono bg-zone-100 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500 cursor-pointer"
                          >
                            <option value="">Assign agent...</option>
                            {agents.map((a) => (
                              <option key={a.id} value={a.id}>
                                {a.codename} — {a.zone_callsign}
                              </option>
                            ))}
                          </select>
                          <select
                            value={newTaskPriority}
                            onChange={(e) => setNewTaskPriority(e.target.value as Priority)}
                            className="text-xs font-mono bg-zone-100 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500 cursor-pointer"
                          >
                            <option value="critical">Critical</option>
                            <option value="high">High</option>
                            <option value="standard">Standard</option>
                            <option value="low">Low</option>
                          </select>
                          <button
                            onClick={() => handleCreate(project.id)}
                            disabled={!newTaskTitle.trim()}
                            className="ml-auto bg-blood-600 text-zone-900 text-xs font-mono uppercase tracking-wider px-4 py-1.5 rounded-sm hover:bg-blood-500 transition-colors disabled:opacity-30 disabled:cursor-not-allowed clip-corner-tl"
                          >
                            Dispatch
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={() => setShowNewTaskForm(project.id)}
                        className="flex items-center gap-2 text-xs font-mono uppercase tracking-widest text-zone-600 hover:text-blood-500 transition-colors"
                      >
                        <Plus className="w-4 h-4" />
                        New Directive
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {projects.length === 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-zone-600">
          <AlertCircle className="w-8 h-8 mb-2" />
          <p className="font-mono text-xs uppercase tracking-widest">No projects initialized</p>
        </div>
      )}
    </div>
  );
}
