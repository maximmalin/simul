import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string;

export const supabase = createClient(supabaseUrl, supabaseAnonKey);

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
export type StepType = 'input' | 'process' | 'decision' | 'review' | 'output';
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
