/**
 * Локальный API-клиент вместо Supabase.
 *
 * Все данные бюро хранятся в JSON-файле на сервере (backend/localdb.py)
 * и доступны через REST-эндпоинты /api/bureau/<table>.
 *
 * Этот модуль сохраняет те же типы данных, что и раньше, чтобы
 * компоненты не пришлось переписыовать.
 */

const BASE = (import.meta.env.VITE_BRIDGE_URL as string | undefined) ?? '';

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------

async function apiRequest<T = any>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  get: <T = any>(path: string) => apiRequest<T>('GET', path),
  post: <T = any>(path: string, body?: unknown) => apiRequest<T>('POST', path, body),
  patch: <T = any>(path: string, body?: unknown) => apiRequest<T>('PATCH', path, body),
  delete: <T = any>(path: string) => apiRequest<T>('DELETE', path),
};

// ---------------------------------------------------------------------------
// Типы данных (те же, что были в Supabase)
// ---------------------------------------------------------------------------

export type AgentStatus = 'active' | 'processing' | 'idle' | 'offline';
export type ProjectStatus = 'planning' | 'active' | 'review' | 'completed';
export type TaskStatus = 'pending' | 'in_progress' | 'review' | 'done';
export type Priority = 'critical' | 'high' | 'standard' | 'low';
export type MessageChannel = 'command' | 'research' | 'engineering' | 'testing' | 'optimization';

export interface Agent {
  id: string;
  codename: string;
  role_key: string;
  role_title: string;
  designation: string;
  status: AgentStatus;
  specialty: string;
  current_task: string | null;
  efficiency: number;
  cycles_completed: number;
  created_at: string;
  bio: string;
  quote: string;
  traits: string[];
  psych_profile: string;
  zone_callsign: string;
}

export interface Project {
  id: string;
  code: string;
  title: string;
  objective: string;
  status: ProjectStatus;
  priority: Priority;
  progress: number;
  created_at: string;
}

export interface Task {
  id: string;
  project_id: string;
  agent_id: string | null;
  title: string;
  directive: string;
  status: TaskStatus;
  priority: Priority;
  sort_order: number;
  created_at: string;
}

export interface Message {
  id: string;
  from_agent_id: string;
  to_agent_id: string | null;
  content: string;
  channel: MessageChannel;
  created_at: string;
}

export interface MessageWithAgents extends Message {
  from_agent?: Pick<Agent, 'id' | 'codename' | 'role_key'>;
  to_agent?: Pick<Agent, 'id' | 'codename' | 'role_key'> | null;
}

export type ChatStatus = 'pending' | 'processing' | 'responded';

export interface ChatRequest {
  id: string;
  content: string;
  project_id: string | null;
  status: ChatStatus;
  response_agent_id: string | null;
  response_text: string | null;
  created_at: string;
  responded_at: string | null;
}

export interface ChatRequestWithAgent extends ChatRequest {
  response_agent?: Pick<Agent, 'id' | 'codename' | 'role_key' | 'role_title' | 'zone_callsign'> | null;
}

export type DeliverableStatus = 'draft' | 'final' | 'published';

export interface Deliverable {
  id: string;
  project_id: string;
  agent_id: string | null;
  title: string;
  description: string;
  file_content: string;
  file_format: string;
  status: DeliverableStatus;
  created_at: string;
}

export interface DeliverableWithRelations extends Deliverable {
  project?: Pick<Project, 'id' | 'code' | 'title'>;
  agent?: Pick<Agent, 'id' | 'codename' | 'zone_callsign' | 'role_key'> | null;
}

export interface WarehouseComponent {
  id: string;
  name: string;
  part_number: string | null;
  manufacturer: string | null;
  category: string;
  package_type: string | null;
  value: string | null;
  tolerance: string | null;
  voltage_rating: string | null;
  power_rating: string | null;
  temperature_range: string | null;
  description: string | null;
  quantity: number;
  location: string | null;
  raw_ocr_text: string | null;
  image_url: string | null;
  datasheet_url: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export type WorkflowStatus = 'draft' | 'active' | 'paused' | 'completed';
export type StepType = 'input' | 'process' | 'decision' | 'output' | 'review';
export type StepStatus = 'pending' | 'active' | 'done' | 'skipped';

export interface FlowWorkflow {
  id: string;
  name: string;
  description: string;
  project_id: string | null;
  status: WorkflowStatus;
  created_at: string;
  updated_at: string;
}

export interface FlowStep {
  id: string;
  workflow_id: string;
  agent_id: string | null;
  title: string;
  directive: string;
  step_type: StepType;
  position_x: number;
  position_y: number;
  sort_order: number;
  status: StepStatus;
  created_at: string;
}

export interface FlowStepWithAgent extends FlowStep {
  agent?: Pick<Agent, 'id' | 'codename' | 'role_key' | 'role_title' | 'zone_callsign'> | null;
}

export interface FlowConnection {
  id: string;
  workflow_id: string;
  from_step_id: string;
  to_step_id: string;
  label: string | null;
  created_at: string;
}

export interface FlowWorkflowWithDetails extends FlowWorkflow {
  project?: Pick<Project, 'id' | 'code' | 'title'> | null;
  steps?: FlowStepWithAgent[];
  connections?: FlowConnection[];
}

export type DevAgentStatus = 'active' | 'coding' | 'reviewing' | 'idle' | 'offline';

export interface DevAgent {
  id: string;
  codename: string;
  role_key: string;
  role_title: string;
  designation: string;
  status: DevAgentStatus;
  specialty: string;
  languages: string[];
  frameworks: string[];
  current_project: string | null;
  git_branch: string | null;
  commits_today: number;
  prs_open: number;
  code_quality: number;
  efficiency: number;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Заглушка для совместимости: SUPABASE_CONFIGURED теперь всегда true
// ---------------------------------------------------------------------------

export const SUPABASE_CONFIGURED = true;

// ---------------------------------------------------------------------------
// Заглушка для `supabase` — простой прокси, чтобы старый код работал
// ---------------------------------------------------------------------------

export const supabase = {
  from: (table: string) => ({
    select: () => api.get(`/api/bureau/${table}`),
    insert: (data: unknown) => api.post(`/api/bureau/${table}`, data),
    update: (data: unknown) => {
      // В Supabase цепочка была .update().eq('id', id)
      // Здесь упрощённо: передаём {id, ...data}
      const id = (data as any).id;
      return api.patch(`/api/bureau/${table}/${id}`, data);
    },
    delete: () => {
      throw new Error('delete() не поддерживается через цепочку — используйте api.delete(path)');
    },
  }),
};

export function getSupabase() {
  return supabase;
}
