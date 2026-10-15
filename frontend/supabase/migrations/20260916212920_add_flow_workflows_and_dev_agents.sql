/*
# Add flow workflows and dev department agents

## Overview
Two new features:
1. Visual flow editor (FloAys-style) — create agent task workflows as node graphs.
   Each workflow has steps (nodes) assigned to agents, connected in sequences (edges).
   Users can drag, reorder, assign, and chain execution steps visually.
2. Dev department — programmer and engineer-programmer agents with their own
   specialty fields, languages, and project assignments.

## New Tables

1. `flow_workflows` — Visual workflow definitions
   - id (uuid PK)
   - name (text) — workflow name
   - description (text)
   - project_id (uuid FK, nullable) — linked project
   - status (text: draft, active, paused, completed)
   - created_at, updated_at

2. `flow_steps` — Nodes within a workflow, each assigned to an agent
   - id (uuid PK)
   - workflow_id (uuid FK -> flow_workflows, CASCADE)
   - agent_id (uuid FK -> bureau_agents, nullable)
   - title (text) — step name
   - directive (text) — what the agent should do
   - step_type (text: input, process, decision, output, review)
   - position_x (int) — canvas X position
   - position_y (int) — canvas Y position
   - sort_order (int) — execution order
   - status (text: pending, active, done, skipped)
   - created_at

3. `flow_connections` — Edges between steps (execution sequence)
   - id (uuid PK)
   - workflow_id (uuid FK -> flow_workflows, CASCADE)
   - from_step_id (uuid FK -> flow_steps, CASCADE)
   - to_step_id (uuid FK -> flow_steps, CASCADE)
   - label (text, nullable) — condition label for decision branches
   - created_at

4. `dev_agents` — Programmer and engineer-programmer agents for the dev department
   - id (uuid PK)
   - codename (text)
   - role_key (text: lead_dev, frontend_dev, backend_dev, devops_eng, qa_eng, sys_arch)
   - role_title (text)
   - designation (text, Russian title)
   - status (text: active, coding, reviewing, idle, offline)
   - specialty (text)
   - languages (text[]) — programming languages
   - frameworks (text[]) — frameworks/tools
   - current_project (text, nullable)
   - git_branch (text, nullable) — current working branch
   - commits_today (int) — commit count
   - prs_open (int) — open pull requests
   - code_quality (int) — 0-100 quality score
   - efficiency (int)
   - created_at

## Security
- RLS enabled on all tables, single-tenant no-auth pattern.
*/

-- FLOW WORKFLOWS
CREATE TABLE IF NOT EXISTS flow_workflows (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  description text NOT NULL DEFAULT '',
  project_id uuid REFERENCES bureau_projects(id) ON DELETE SET NULL,
  status text NOT NULL DEFAULT 'draft',
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

ALTER TABLE flow_workflows ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_workflows" ON flow_workflows;
CREATE POLICY "anon_select_workflows" ON flow_workflows FOR SELECT
  TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_workflows" ON flow_workflows;
CREATE POLICY "anon_insert_workflows" ON flow_workflows FOR INSERT
  TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_workflows" ON flow_workflows;
CREATE POLICY "anon_update_workflows" ON flow_workflows FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_workflows" ON flow_workflows;
CREATE POLICY "anon_delete_workflows" ON flow_workflows FOR DELETE
  TO anon, authenticated USING (true);

-- FLOW STEPS
CREATE TABLE IF NOT EXISTS flow_steps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id uuid NOT NULL REFERENCES flow_workflows(id) ON DELETE CASCADE,
  agent_id uuid REFERENCES bureau_agents(id) ON DELETE SET NULL,
  title text NOT NULL,
  directive text NOT NULL DEFAULT '',
  step_type text NOT NULL DEFAULT 'process',
  position_x int NOT NULL DEFAULT 0,
  position_y int NOT NULL DEFAULT 0,
  sort_order int NOT NULL DEFAULT 0,
  status text NOT NULL DEFAULT 'pending',
  created_at timestamptz DEFAULT now()
);

ALTER TABLE flow_steps ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_steps" ON flow_steps;
CREATE POLICY "anon_select_steps" ON flow_steps FOR SELECT
  TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_steps" ON flow_steps;
CREATE POLICY "anon_insert_steps" ON flow_steps FOR INSERT
  TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_steps" ON flow_steps;
CREATE POLICY "anon_update_steps" ON flow_steps FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_steps" ON flow_steps;
CREATE POLICY "anon_delete_steps" ON flow_steps FOR DELETE
  TO anon, authenticated USING (true);

-- FLOW CONNECTIONS
CREATE TABLE IF NOT EXISTS flow_connections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workflow_id uuid NOT NULL REFERENCES flow_workflows(id) ON DELETE CASCADE,
  from_step_id uuid NOT NULL REFERENCES flow_steps(id) ON DELETE CASCADE,
  to_step_id uuid NOT NULL REFERENCES flow_steps(id) ON DELETE CASCADE,
  label text,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE flow_connections ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_connections" ON flow_connections;
CREATE POLICY "anon_select_connections" ON flow_connections FOR SELECT
  TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_connections" ON flow_connections;
CREATE POLICY "anon_insert_connections" ON flow_connections FOR INSERT
  TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_connections" ON flow_connections;
CREATE POLICY "anon_delete_connections" ON flow_connections FOR DELETE
  TO anon, authenticated USING (true);

-- INDEXES
CREATE INDEX IF NOT EXISTS idx_steps_workflow ON flow_steps(workflow_id);
CREATE INDEX IF NOT EXISTS idx_steps_agent ON flow_steps(agent_id);
CREATE INDEX IF NOT EXISTS idx_connections_workflow ON flow_connections(workflow_id);
CREATE INDEX IF NOT EXISTS idx_connections_from ON flow_connections(from_step_id);
CREATE INDEX IF NOT EXISTS idx_connections_to ON flow_connections(to_step_id);

-- DEV AGENTS
CREATE TABLE IF NOT EXISTS dev_agents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  codename text NOT NULL,
  role_key text NOT NULL,
  role_title text NOT NULL,
  designation text NOT NULL,
  status text NOT NULL DEFAULT 'idle',
  specialty text NOT NULL,
  languages text[] NOT NULL DEFAULT '{}',
  frameworks text[] NOT NULL DEFAULT '{}',
  current_project text,
  git_branch text,
  commits_today int NOT NULL DEFAULT 0,
  prs_open int NOT NULL DEFAULT 0,
  code_quality int NOT NULL DEFAULT 85,
  efficiency int NOT NULL DEFAULT 85,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE dev_agents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_dev_agents" ON dev_agents;
CREATE POLICY "anon_select_dev_agents" ON dev_agents FOR SELECT
  TO anon, authenticated USING (true);
DROP POLICY IF EXISTS "anon_insert_dev_agents" ON dev_agents;
CREATE POLICY "anon_insert_dev_agents" ON dev_agents FOR INSERT
  TO anon, authenticated WITH CHECK (true);
DROP POLICY IF EXISTS "anon_update_dev_agents" ON dev_agents;
CREATE POLICY "anon_update_dev_agents" ON dev_agents FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "anon_delete_dev_agents" ON dev_agents;
CREATE POLICY "anon_delete_dev_agents" ON dev_agents FOR DELETE
  TO anon, authenticated USING (true);

CREATE INDEX IF NOT EXISTS idx_dev_agents_role ON dev_agents(role_key);

-- SEED: DEV AGENTS (6 programmer/engineer agents)
INSERT INTO dev_agents (codename, role_key, role_title, designation, status, specialty, languages, frameworks, current_project, git_branch, commits_today, prs_open, code_quality, efficiency) VALUES
('KOD-01', 'lead_dev', 'Lead Developer', 'Ведущий Разработчик', 'coding', 'Architecture, code review, technical leadership', ARRAY['TypeScript', 'Rust', 'Python'], ARRAY['React', 'Vite', 'Deno', 'Tokio'], 'Bureau Core Platform', 'feat/flow-editor', 14, 3, 96, 94),
('FRNT-02', 'frontend_dev', 'Frontend Developer', 'Фронтенд Разработчик', 'coding', 'UI/UX implementation, component design, state management', ARRAY['TypeScript', 'JavaScript', 'CSS'], ARRAY['React', 'Vite', 'Tailwind', 'Zustand'], 'Bureau Core Platform', 'feat/flow-editor', 23, 5, 91, 89),
('BAK-03', 'backend_dev', 'Backend Developer', 'Бэкенд Разработчик', 'reviewing', 'API design, database architecture, edge functions', ARRAY['TypeScript', 'SQL', 'Go'], ARRAY['Deno', 'Supabase', 'PostgreSQL', 'Hono'], 'Bureau Core Platform', 'fix/edge-cors', 8, 2, 93, 92),
('OPS-04', 'devops_eng', 'DevOps Engineer', 'Инженер DevOps', 'active', 'CI/CD, infrastructure, deployment automation', ARRAY['Bash', 'YAML', 'Dockerfile'], ARRAY['Docker', 'GitHub Actions', 'Supabase', 'Vercel'], 'Bureau Core Platform', 'main', 5, 1, 88, 87),
('QAT-05', 'qa_eng', 'QA Engineer', 'Инженер Тестирования', 'coding', 'Automated testing, integration validation, e2e flows', ARRAY['TypeScript', 'Python'], ARRAY['Vitest', 'Playwright', 'Pytest', 'Supabase'], 'Bureau Core Platform', 'test/flow-e2e', 11, 4, 90, 86),
('ARC-06', 'sys_arch', 'Systems Architect', 'Системный Архитектор', 'active', 'System design, data flow, performance optimization', ARRAY['Rust', 'TypeScript', 'SQL'], ARRAY['Tokio', 'Deno', 'PostgreSQL', 'Redis'], 'Bureau Core Platform', 'feat/pipeline-arch', 6, 2, 97, 93)
ON CONFLICT DO NOTHING;

-- SEED: SAMPLE FLOW WORKFLOW
INSERT INTO flow_workflows (name, description, status)
SELECT 'Chrysalis Phase III — Execution Flow', 'Sequential agent workflow for Phase III lattice propagation development', 'active'
FROM bureau_projects WHERE code = 'PROJECT-CHRYN-01'
LIMIT 1
ON CONFLICT DO NOTHING;

-- SEED: FLOW STEPS for the sample workflow
INSERT INTO flow_steps (workflow_id, agent_id, title, directive, step_type, position_x, position_y, sort_order, status)
SELECT w.id, a.id, 'Define Propagation Parameters', 'Establish baseline constants for lattice propagation under variable thermal load', 'input', 80, 120, 0, 'done'
FROM flow_workflows w, bureau_agents a
WHERE w.name = 'Chrysalis Phase III — Execution Flow' AND a.codename = 'HERMES-02'
ON CONFLICT DO NOTHING;

INSERT INTO flow_steps (workflow_id, agent_id, title, directive, step_type, position_x, position_y, sort_order, status)
SELECT w.id, a.id, 'Design Chassis Topology', 'Create structural framework supporting dynamic lattice reconfiguration', 'process', 340, 80, 1, 'active'
FROM flow_workflows w, bureau_agents a
WHERE w.name = 'Chrysalis Phase III — Execution Flow' AND a.codename = 'ARKON-03'
ON CONFLICT DO NOTHING;

INSERT INTO flow_steps (workflow_id, agent_id, title, directive, step_type, position_x, position_y, sort_order, status)
SELECT w.id, a.id, 'Analyze Test Results', 'Process telemetry from Batch 1-6 and identify deviation patterns', 'process', 340, 220, 2, 'active'
FROM flow_workflows w, bureau_agents a
WHERE w.name = 'Chrysalis Phase III — Execution Flow' AND a.codename = 'VEKTOR-04'
ON CONFLICT DO NOTHING;

INSERT INTO flow_steps (workflow_id, agent_id, title, directive, step_type, position_x, position_y, sort_order, status)
SELECT w.id, a.id, 'Stress-Test Lattice', 'Subject prototype chassis to extreme thermal cycling and record failure modes', 'review', 600, 150, 3, 'pending'
FROM flow_workflows w, bureau_agents a
WHERE w.name = 'Chrysalis Phase III — Execution Flow' AND a.codename = 'ISPYT-05'
ON CONFLICT DO NOTHING;

INSERT INTO flow_steps (workflow_id, agent_id, title, directive, step_type, position_x, position_y, sort_order, status)
SELECT w.id, a.id, 'Optimize Pipeline', 'Tune lattice parameters and buffer allocation for maximum throughput', 'output', 860, 150, 4, 'pending'
FROM flow_workflows w, bureau_agents a
WHERE w.name = 'Chrysalis Phase III — Execution Flow' AND a.codename = 'OPTIM-06'
ON CONFLICT DO NOTHING;

-- SEED: FLOW CONNECTIONS (edges between steps)
INSERT INTO flow_connections (workflow_id, from_step_id, to_step_id)
SELECT w.id, s1.id, s2.id
FROM flow_workflows w, flow_steps s1, flow_steps s2
WHERE w.name = 'Chrysalis Phase III — Execution Flow'
  AND s1.workflow_id = w.id AND s2.workflow_id = w.id
  AND s1.title = 'Define Propagation Parameters' AND s2.title = 'Design Chassis Topology'
ON CONFLICT DO NOTHING;

INSERT INTO flow_connections (workflow_id, from_step_id, to_step_id)
SELECT w.id, s1.id, s2.id
FROM flow_workflows w, flow_steps s1, flow_steps s2
WHERE w.name = 'Chrysalis Phase III — Execution Flow'
  AND s1.workflow_id = w.id AND s2.workflow_id = w.id
  AND s1.title = 'Define Propagation Parameters' AND s2.title = 'Analyze Test Results'
ON CONFLICT DO NOTHING;

INSERT INTO flow_connections (workflow_id, from_step_id, to_step_id)
SELECT w.id, s1.id, s2.id
FROM flow_workflows w, flow_steps s1, flow_steps s2
WHERE w.name = 'Chrysalis Phase III — Execution Flow'
  AND s1.workflow_id = w.id AND s2.workflow_id = w.id
  AND s1.title = 'Design Chassis Topology' AND s2.title = 'Stress-Test Lattice'
ON CONFLICT DO NOTHING;

INSERT INTO flow_connections (workflow_id, from_step_id, to_step_id)
SELECT w.id, s1.id, s2.id
FROM flow_workflows w, flow_steps s1, flow_steps s2
WHERE w.name = 'Chrysalis Phase III — Execution Flow'
  AND s1.workflow_id = w.id AND s2.workflow_id = w.id
  AND s1.title = 'Analyze Test Results' AND s2.title = 'Stress-Test Lattice'
ON CONFLICT DO NOTHING;

INSERT INTO flow_connections (workflow_id, from_step_id, to_step_id)
SELECT w.id, s1.id, s2.id
FROM flow_workflows w, flow_steps s1, flow_steps s2
WHERE w.name = 'Chrysalis Phase III — Execution Flow'
  AND s1.workflow_id = w.id AND s2.workflow_id = w.id
  AND s1.title = 'Stress-Test Lattice' AND s2.title = 'Optimize Pipeline'
ON CONFLICT DO NOTHING;