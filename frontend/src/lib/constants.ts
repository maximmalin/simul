import {
  Skull,
  Brain,
  DraftingCompass,
  ScanLine,
  FlaskRound,
  Gauge,
  Cpu,
  type LucideIcon,
} from 'lucide-react';
import type { AgentStatus, ProjectStatus, TaskStatus, Priority, MessageChannel } from './supabase';

export const ROLE_ICONS: Record<string, LucideIcon> = {
  chief: Skull,
  theorist: Brain,
  designer: DraftingCompass,
  analyst: ScanLine,
  tester: FlaskRound,
  optimizer: Gauge,
};

export const ROLE_LABELS: Record<string, string> = {
  chief: 'Chief Engineer',
  theorist: 'Chief Theorist',
  designer: 'Chief Constructor',
  analyst: 'Senior Analyst',
  tester: 'Chief Tester',
  optimizer: 'Optimization Specialist',
};

export const STATUS_CONFIG: Record<AgentStatus, { label: string; color: string; dot: string }> = {
  active: { label: 'Active', color: 'text-toxic-400', dot: 'status-dot-active' },
  processing: { label: 'Processing', color: 'text-amber-500', dot: 'status-dot-processing' },
  idle: { label: 'Standby', color: 'text-zone-700', dot: 'status-dot-idle' },
  offline: { label: 'Offline', color: 'text-blood-500', dot: 'status-dot-offline' },
};

export const PROJECT_STATUS_CONFIG: Record<ProjectStatus, { label: string; color: string; bg: string; border: string }> = {
  planning: { label: 'Planning', color: 'text-steel-400', bg: 'bg-steel-700/30', border: 'border-steel-600' },
  active: { label: 'Active', color: 'text-toxic-400', bg: 'bg-toxic-700/20', border: 'border-toxic-600' },
  review: { label: 'Under Review', color: 'text-amber-400', bg: 'bg-amber-600/15', border: 'border-amber-500' },
  completed: { label: 'Completed', color: 'text-zone-700', bg: 'bg-zone-400/20', border: 'border-zone-500' },
};

export const TASK_STATUS_CONFIG: Record<TaskStatus, { label: string; color: string; bg: string; border: string }> = {
  pending: { label: 'Queued', color: 'text-zone-800', bg: 'bg-zone-300/30', border: 'border-zone-500/40' },
  in_progress: { label: 'Active', color: 'text-amber-400', bg: 'bg-amber-600/10', border: 'border-amber-500/40' },
  review: { label: 'Review', color: 'text-toxic-400', bg: 'bg-toxic-700/10', border: 'border-toxic-600/40' },
  done: { label: 'Closed', color: 'text-zone-700', bg: 'bg-zone-400/10', border: 'border-zone-600/40' },
};

export const PRIORITY_CONFIG: Record<Priority, { label: string; color: string; bg: string }> = {
  critical: { label: 'CRITICAL', color: 'text-blood-500', bg: 'bg-blood-900/30' },
  high: { label: 'HIGH', color: 'text-amber-400', bg: 'bg-amber-600/15' },
  standard: { label: 'STANDARD', color: 'text-zone-800', bg: 'bg-zone-400/15' },
  low: { label: 'LOW', color: 'text-zone-600', bg: 'bg-zone-300/10' },
};

export const CHANNEL_CONFIG: Record<MessageChannel, { label: string; color: string; bg: string; border: string }> = {
  command: { label: 'CMD', color: 'text-blood-500', bg: 'bg-blood-900/20', border: 'border-blood-700/40' },
  research: { label: 'RES', color: 'text-toxic-400', bg: 'bg-toxic-700/15', border: 'border-toxic-600/30' },
  engineering: { label: 'ENG', color: 'text-amber-400', bg: 'bg-amber-600/10', border: 'border-amber-500/30' },
  testing: { label: 'TST', color: 'text-rust-400', bg: 'bg-rust-800/20', border: 'border-rust-600/30' },
  optimization: { label: 'OPT', color: 'text-steel-400', bg: 'bg-steel-700/20', border: 'border-steel-600/30' },
};

export const TASK_STATUSES: TaskStatus[] = ['pending', 'in_progress', 'review', 'done'];

import type {
  WorkflowStatus,
  StepType,
  StepStatus,
  DevAgentStatus,
} from './supabase';

export const WORKFLOW_STATUS_CONFIG: Record<WorkflowStatus, { label: string; color: string; bg: string; border: string }> = {
  draft: { label: 'Draft', color: 'text-zone-600', bg: 'bg-zone-400/15', border: 'border-zone-500/30' },
  active: { label: 'Active', color: 'text-toxic-400', bg: 'bg-toxic-700/15', border: 'border-toxic-600/30' },
  paused: { label: 'Paused', color: 'text-amber-400', bg: 'bg-amber-600/15', border: 'border-amber-500/30' },
  completed: { label: 'Completed', color: 'text-zone-700', bg: 'bg-zone-400/20', border: 'border-zone-500/40' },
};

export const STEP_TYPE_CONFIG: Record<StepType, { label: string; color: string; bg: string; border: string }> = {
  input: { label: 'Input', color: 'text-steel-400', bg: 'bg-steel-700/20', border: 'border-steel-600/40' },
  process: { label: 'Process', color: 'text-toxic-400', bg: 'bg-toxic-700/15', border: 'border-toxic-600/30' },
  decision: { label: 'Decision', color: 'text-amber-400', bg: 'bg-amber-600/15', border: 'border-amber-500/30' },
  review: { label: 'Review', color: 'text-rust-400', bg: 'bg-rust-800/20', border: 'border-rust-600/30' },
  output: { label: 'Output', color: 'text-blood-500', bg: 'bg-blood-900/20', border: 'border-blood-700/40' },
};

export const STEP_STATUS_CONFIG: Record<StepStatus, { label: string; color: string; dot: string }> = {
  pending: { label: 'Queued', color: 'text-zone-600', dot: 'status-dot-idle' },
  active: { label: 'Active', color: 'text-amber-400', dot: 'status-dot-processing' },
  done: { label: 'Done', color: 'text-toxic-400', dot: 'status-dot-active' },
  skipped: { label: 'Skipped', color: 'text-zone-600/50', dot: 'status-dot-offline' },
};

export const DEV_ROLE_ICONS: Record<string, LucideIcon> = {
  lead_dev: Skull,
  frontend_dev: DraftingCompass,
  backend_dev: Cpu,
  devops_eng: Gauge,
  qa_eng: FlaskRound,
  sys_arch: Brain,
};

export const DEV_ROLE_LABELS: Record<string, string> = {
  lead_dev: 'Lead Developer',
  frontend_dev: 'Frontend Developer',
  backend_dev: 'Backend Developer',
  devops_eng: 'DevOps Engineer',
  qa_eng: 'QA Engineer',
  sys_arch: 'Systems Architect',
};

export const DEV_STATUS_CONFIG: Record<DevAgentStatus, { label: string; color: string; dot: string }> = {
  active: { label: 'Active', color: 'text-toxic-400', dot: 'status-dot-active' },
  coding: { label: 'Coding', color: 'text-amber-400', dot: 'status-dot-processing' },
  reviewing: { label: 'Reviewing', color: 'text-steel-400', dot: 'status-dot-processing' },
  idle: { label: 'Standby', color: 'text-zone-700', dot: 'status-dot-idle' },
  offline: { label: 'Offline', color: 'text-blood-500', dot: 'status-dot-offline' },
};
