/*
# Create Seren + Hermes Engineering Bureau Schema

## Overview
Single-tenant (no auth) schema for a virtual multi-agent engineering bureau.
Agents with distinct roles collaborate on projects via tasks and inter-agent messages.

## New Tables

1. `bureau_agents` — The bureau's AI agents
   - id (uuid PK)
   - codename (text, e.g. "SEREN-01")
   - role_key (text: chief, theorist, designer, analyst, tester, optimizer)
   - role_title (text, English title)
   - designation (text, Russian constructivist title)
   - status (text: active, processing, idle, offline)
   - specialty (text)
   - current_task (text, nullable)
   - efficiency (int 0-100)
   - cycles_completed (int)
   - created_at

2. `bureau_projects` — Engineering projects
   - id (uuid PK)
   - code (text, e.g. "PROJECT-CHRYN-01")
   - title (text)
   - objective (text)
   - status (text: planning, active, review, completed)
   - priority (text: critical, high, standard, low)
   - progress (int 0-100)
   - created_at

3. `bureau_tasks` — Tasks within projects, assigned to agents
   - id (uuid PK)
   - project_id (uuid FK -> bureau_projects)
   - agent_id (uuid FK -> bureau_agents, nullable for unassigned)
   - title (text)
   - directive (text)
   - status (text: pending, in_progress, review, done)
   - priority (text)
   - sort_order (int)
   - created_at

4. `bureau_messages` — Inter-agent communications
   - id (uuid PK)
   - from_agent_id (uuid FK -> bureau_agents)
   - to_agent_id (uuid FK -> bureau_agents, nullable for broadcast)
   - content (text)
   - channel (text: command, research, engineering, testing, optimization)
   - created_at

## Security
- RLS enabled on all tables.
- Single-tenant no-auth app: all policies use TO anon, authenticated with USING(true)
  because the data is intentionally public/shared.

## Seed Data
- 6 agents (Chief, Theorist, Designer, Analyst, Tester, Optimizer)
- 3 projects
- 8 tasks across projects
- 5 inter-agent messages
*/

-- AGENTS
CREATE TABLE IF NOT EXISTS bureau_agents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  codename text NOT NULL,
  role_key text NOT NULL,
  role_title text NOT NULL,
  designation text NOT NULL,
  status text NOT NULL DEFAULT 'idle',
  specialty text NOT NULL,
  current_task text,
  efficiency int NOT NULL DEFAULT 85,
  cycles_completed int NOT NULL DEFAULT 0,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE bureau_agents ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_agents" ON bureau_agents;
CREATE POLICY "anon_select_agents" ON bureau_agents FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_agents" ON bureau_agents;
CREATE POLICY "anon_insert_agents" ON bureau_agents FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_agents" ON bureau_agents;
CREATE POLICY "anon_update_agents" ON bureau_agents FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_agents" ON bureau_agents;
CREATE POLICY "anon_delete_agents" ON bureau_agents FOR DELETE
  TO anon, authenticated USING (true);

-- PROJECTS
CREATE TABLE IF NOT EXISTS bureau_projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text NOT NULL,
  title text NOT NULL,
  objective text NOT NULL,
  status text NOT NULL DEFAULT 'planning',
  priority text NOT NULL DEFAULT 'standard',
  progress int NOT NULL DEFAULT 0,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE bureau_projects ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_projects" ON bureau_projects;
CREATE POLICY "anon_select_projects" ON bureau_projects FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_projects" ON bureau_projects;
CREATE POLICY "anon_insert_projects" ON bureau_projects FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_projects" ON bureau_projects;
CREATE POLICY "anon_update_projects" ON bureau_projects FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_projects" ON bureau_projects;
CREATE POLICY "anon_delete_projects" ON bureau_projects FOR DELETE
  TO anon, authenticated USING (true);

-- TASKS
CREATE TABLE IF NOT EXISTS bureau_tasks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES bureau_projects(id) ON DELETE CASCADE,
  agent_id uuid REFERENCES bureau_agents(id) ON DELETE SET NULL,
  title text NOT NULL,
  directive text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'pending',
  priority text NOT NULL DEFAULT 'standard',
  sort_order int NOT NULL DEFAULT 0,
  created_at timestamptz DEFAULT now()
);

ALTER TABLE bureau_tasks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_tasks" ON bureau_tasks;
CREATE POLICY "anon_select_tasks" ON bureau_tasks FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_tasks" ON bureau_tasks;
CREATE POLICY "anon_insert_tasks" ON bureau_tasks FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_tasks" ON bureau_tasks;
CREATE POLICY "anon_update_tasks" ON bureau_tasks FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_tasks" ON bureau_tasks;
CREATE POLICY "anon_delete_tasks" ON bureau_tasks FOR DELETE
  TO anon, authenticated USING (true);

-- MESSAGES
CREATE TABLE IF NOT EXISTS bureau_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  from_agent_id uuid NOT NULL REFERENCES bureau_agents(id) ON DELETE CASCADE,
  to_agent_id uuid REFERENCES bureau_agents(id) ON DELETE SET NULL,
  content text NOT NULL,
  channel text NOT NULL DEFAULT 'command',
  created_at timestamptz DEFAULT now()
);

ALTER TABLE bureau_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_messages" ON bureau_messages;
CREATE POLICY "anon_select_messages" ON bureau_messages FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_messages" ON bureau_messages;
CREATE POLICY "anon_insert_messages" ON bureau_messages FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_messages" ON bureau_messages;
CREATE POLICY "anon_delete_messages" ON bureau_messages FOR DELETE
  TO anon, authenticated USING (true);

-- INDEXES
CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON bureau_tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_agent_id ON bureau_tasks(agent_id);
CREATE INDEX IF NOT EXISTS idx_messages_from_agent ON bureau_messages(from_agent_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON bureau_messages(created_at DESC);

-- SEED: AGENTS
INSERT INTO bureau_agents (codename, role_key, role_title, designation, status, specialty, current_task, efficiency, cycles_completed) VALUES
('SEREN-01', 'chief', 'Chief Engineer', 'Главный Инженер', 'active', 'Strategic oversight and resource allocation', 'Coordinating Project Chrysalis', 96, 1247),
('HERMES-02', 'theorist', 'Chief Theorist', 'Главный Теоретик', 'processing', 'Theoretical frameworks and hypothesis generation', 'Deriving propagation models for Phase III', 92, 983),
('ARKON-03', 'designer', 'Chief Constructor', 'Главный Конструктор', 'processing', 'Systems architecture and structural design', 'Designing lattice topology for chassis', 89, 871),
('VEKTOR-04', 'analyst', 'Senior Analyst', 'Старший Аналитик', 'active', 'Data analysis and pattern recognition', 'Processing telemetry from Test Batch 7', 94, 1102),
('ISPYT-05', 'tester', 'Chief Tester', 'Главный Испытатель', 'idle', 'Validation, stress testing, and failure analysis', NULL, 87, 756),
('OPTIM-06', 'optimizer', 'Optimization Specialist', 'Специалист по Оптимизации', 'idle', 'Performance tuning and resource optimization', NULL, 91, 644)
ON CONFLICT DO NOTHING;

-- SEED: PROJECTS
INSERT INTO bureau_projects (code, title, objective, status, priority, progress) VALUES
('PROJECT-CHRYN-01', 'Project Chrysalis', 'Develop autonomous lattice-based propulsion framework with adaptive topology', 'active', 'critical', 67),
('PROJECT-VEKT-02', 'Project Vektor', 'Design and validate high-fidelity telemetry pipeline for real-time anomaly detection', 'active', 'high', 42),
('PROJECT-NOVA-03', 'Project Novaya Zarya', 'Establish theoretical foundation for next-generation energy coupling matrices', 'planning', 'standard', 12)
ON CONFLICT DO NOTHING;

-- SEED: TASKS (using subqueries to get project and agent IDs)
INSERT INTO bureau_tasks (project_id, agent_id, title, directive, status, priority, sort_order)
SELECT p.id, a.id, 'Define lattice propagation parameters', 'Establish baseline constants for lattice propagation under variable thermal load', 'done', 'high', 1
FROM bureau_projects p, bureau_agents a
WHERE p.code = 'PROJECT-CHRYN-01' AND a.codename = 'HERMES-02'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_tasks (project_id, agent_id, title, directive, status, priority, sort_order)
SELECT p.id, a.id, 'Design adaptive chassis topology', 'Create structural framework supporting dynamic lattice reconfiguration', 'in_progress', 'critical', 2
FROM bureau_projects p, bureau_agents a
WHERE p.code = 'PROJECT-CHRYN-01' AND a.codename = 'ARKON-03'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_tasks (project_id, agent_id, title, directive, status, priority, sort_order)
SELECT p.id, a.id, 'Analyze propagation test results', 'Process telemetry from Batch 1-6 and identify deviation patterns', 'in_progress', 'high', 3
FROM bureau_projects p, bureau_agents a
WHERE p.code = 'PROJECT-CHRYN-01' AND a.codename = 'VEKTOR-04'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_tasks (project_id, agent_id, title, directive, status, priority, sort_order)
SELECT p.id, a.id, 'Stress-test lattice integrity', 'Subject prototype chassis to extreme thermal cycling and record failure modes', 'pending', 'critical', 4
FROM bureau_projects p, bureau_agents a
WHERE p.code = 'PROJECT-CHRYN-01' AND a.codename = 'ISPYT-05'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_tasks (project_id, agent_id, title, directive, status, priority, sort_order)
SELECT p.id, a.id, 'Build telemetry ingestion pipeline', 'Construct data pipeline capable of processing 2M events/sec with sub-50ms latency', 'in_progress', 'high', 1
FROM bureau_projects p, bureau_agents a
WHERE p.code = 'PROJECT-VEKT-02' AND a.codename = 'ARKON-03'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_tasks (project_id, agent_id, title, directive, status, priority, sort_order)
SELECT p.id, a.id, 'Define anomaly detection heuristics', 'Establish statistical baselines and threshold models for real-time anomaly flagging', 'review', 'high', 2
FROM bureau_projects p, bureau_agents a
WHERE p.code = 'PROJECT-VEKT-02' AND a.codename = 'VEKTOR-04'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_tasks (project_id, agent_id, title, directive, status, priority, sort_order)
SELECT p.id, a.id, 'Derive energy coupling equations', 'Formulate mathematical framework for multi-axis energy coupling under variable field conditions', 'in_progress', 'standard', 1
FROM bureau_projects p, bureau_agents a
WHERE p.code = 'PROJECT-NOVA-03' AND a.codename = 'HERMES-02'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_tasks (project_id, agent_id, title, directive, status, priority, sort_order)
SELECT p.id, NULL, 'Review coupling matrix feasibility', 'Cross-check derived equations against known physical constraints and prior experimental data', 'pending', 'standard', 2
FROM bureau_projects p
WHERE p.code = 'PROJECT-NOVA-03'
ON CONFLICT DO NOTHING;

-- SEED: MESSAGES
INSERT INTO bureau_messages (from_agent_id, to_agent_id, content, channel)
SELECT a1.id, a2.id, 'All units: Project Chrysalis Phase III commencing. HERMES-02, prioritize propagation models. ARKON-03, chassis design must align with new parameters by 1800.', 'command'
FROM bureau_agents a1, bureau_agents a2
WHERE a1.codename = 'SEREN-01' AND a2.codename = 'HERMES-02'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_messages (from_agent_id, to_agent_id, content, channel)
SELECT a1.id, a2.id, 'Propagation models indicate thermal variance exceeds initial estimates by 14%. Recommend lattice density increase in sector 7-G before chassis finalization.', 'research'
FROM bureau_agents a1, bureau_agents a2
WHERE a1.codename = 'HERMES-02' AND a2.codename = 'ARKON-03'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_messages (from_agent_id, to_agent_id, content, channel)
SELECT a1.id, a2.id, 'Acknowledged. Adjusting lattice topology — will incorporate 7-G reinforcement. Request VEKTOR-04 provide historical thermal data for calibration.', 'engineering'
FROM bureau_agents a1, bureau_agents a2
WHERE a1.codename = 'ARKON-03' AND a2.codename = 'VEKTOR-04'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_messages (from_agent_id, to_agent_id, content, channel)
SELECT a1.id, a2.id, 'Telemetry from Batch 7 processed. Confirmed 14.3% thermal variance — data package transmitted to ARKON-03. Anomaly cluster detected in sector 7-G, corroborating HERMES-02 findings.', 'research'
FROM bureau_agents a1, bureau_agents a2
WHERE a1.codename = 'VEKTOR-04' AND a2.codename = 'ARKON-03'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_messages (from_agent_id, to_agent_id, content, channel)
SELECT a1.id, NULL, 'BROADCAST: Chrysalis Phase III parameters locked. All agents confirm readiness. Next review cycle at 0600.', 'command'
FROM bureau_agents a1
WHERE a1.codename = 'SEREN-01'
ON CONFLICT DO NOTHING;