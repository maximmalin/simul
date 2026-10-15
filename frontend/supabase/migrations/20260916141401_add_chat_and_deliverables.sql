/*
# Add chat requests and deliverables tables

## Overview
Enables a user-to-bureau chat flow where a human operator sends a message to the bureau,
the backend routes it to the appropriate agent (SEREN-01 chief), creates a tech task from it,
and agents produce deliverables (final results) that the user can download.

## New Tables

1. `bureau_chat_requests` — Messages from the human operator to the bureau
   - id (uuid PK)
   - content (text) — the user's message
   - project_id (uuid FK, nullable) — optional project to attach the task to
   - status (text: pending, processing, responded) — tracks processing state
   - response_agent_id (uuid FK, nullable) — which agent responded
   - response_text (text, nullable) — the agent's response
   - created_at, responded_at (timestamptz)

2. `bureau_deliverables` — Final results produced by agents, downloadable by user
   - id (uuid PK)
   - project_id (uuid FK -> bureau_projects)
   - agent_id (uuid FK -> bureau_agents) — which agent produced it
   - title (text) — deliverable name
   - description (text) — what it is
   - file_content (text) — the actual content (text-based deliverable)
   - file_format (text: e.g. 'txt', 'json', 'md', 'csv')
   - status (text: draft, final, published)
   - created_at (timestamptz)

## Security
- RLS enabled on both tables.
- Single-tenant no-auth: TO anon, authenticated with USING(true) — same pattern as existing tables.
*/

-- CHAT REQUESTS
CREATE TABLE IF NOT EXISTS bureau_chat_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  content text NOT NULL,
  project_id uuid REFERENCES bureau_projects(id) ON DELETE SET NULL,
  status text NOT NULL DEFAULT 'pending',
  response_agent_id uuid REFERENCES bureau_agents(id) ON DELETE SET NULL,
  response_text text,
  created_at timestamptz DEFAULT now(),
  responded_at timestamptz
);

ALTER TABLE bureau_chat_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_chat_requests" ON bureau_chat_requests;
CREATE POLICY "anon_select_chat_requests" ON bureau_chat_requests FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_chat_requests" ON bureau_chat_requests;
CREATE POLICY "anon_insert_chat_requests" ON bureau_chat_requests FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_chat_requests" ON bureau_chat_requests;
CREATE POLICY "anon_update_chat_requests" ON bureau_chat_requests FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_chat_requests" ON bureau_chat_requests;
CREATE POLICY "anon_delete_chat_requests" ON bureau_chat_requests FOR DELETE
  TO anon, authenticated USING (true);

-- DELIVERABLES
CREATE TABLE IF NOT EXISTS bureau_deliverables (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES bureau_projects(id) ON DELETE CASCADE,
  agent_id uuid REFERENCES bureau_agents(id) ON DELETE SET NULL,
  title text NOT NULL,
  description text NOT NULL DEFAULT '',
  file_content text NOT NULL DEFAULT '',
  file_format text NOT NULL DEFAULT 'txt',
  status text NOT NULL DEFAULT 'draft',
  created_at timestamptz DEFAULT now()
);

ALTER TABLE bureau_deliverables ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_deliverables" ON bureau_deliverables;
CREATE POLICY "anon_select_deliverables" ON bureau_deliverables FOR SELECT
  TO anon, authenticated USING (true);

DROP POLICY IF EXISTS "anon_insert_deliverables" ON bureau_deliverables;
CREATE POLICY "anon_insert_deliverables" ON bureau_deliverables FOR INSERT
  TO anon, authenticated WITH CHECK (true);

DROP POLICY IF EXISTS "anon_update_deliverables" ON bureau_deliverables;
CREATE POLICY "anon_update_deliverables" ON bureau_deliverables FOR UPDATE
  TO anon, authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "anon_delete_deliverables" ON bureau_deliverables;
CREATE POLICY "anon_delete_deliverables" ON bureau_deliverables FOR DELETE
  TO anon, authenticated USING (true);

-- INDEXES
CREATE INDEX IF NOT EXISTS idx_chat_requests_created ON bureau_chat_requests(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_deliverables_project ON bureau_deliverables(project_id);
CREATE INDEX IF NOT EXISTS idx_deliverables_created ON bureau_deliverables(created_at DESC);

-- SEED: sample deliverables
INSERT INTO bureau_deliverables (project_id, agent_id, title, description, file_content, file_format, status)
SELECT p.id, a.id, 'Lattice Propagation Model — Phase III', 'Derived mathematical framework for adaptive lattice propagation under variable thermal load. Includes correction coefficients for sector 7-G anomaly cluster.', '== Lattice Propagation Model v3.1 ==

Author: HERMES-02 (Chief Theorist)
Project: PROJECT-CHRYN-01
Date: 2026-09-14

## 1. Base Equation

Ψ(x,t) = Σᵢ αᵢ · φᵢ(x) · exp(-λᵢt)

Where:
  αᵢ = coupling coefficient for lattice node i
  φᵢ = eigenfunction of thermal distribution
  λᵢ = decay constant (thermal)

## 2. Thermal Correction

ΔΨ = 0.143 · Ψ · (∂T/∂x)²

Applied in sector 7-G where thermal variance exceeds 14% baseline.

## 3. Lattice Density Recommendation

Optimal density: 14.7 nodes/cm³ (up from 12.3)
Reinforcement zone: 7-G, radius 2.4m

## 4. Validation Status

Cross-checked against VEKTOR-04 telemetry Batch 7.
Deviation: < 0.3% (within tolerance).

== END OF DOCUMENT ==', 'md', 'final'
FROM bureau_projects p, bureau_agents a
WHERE p.code = 'PROJECT-CHRYN-01' AND a.codename = 'HERMES-02'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_deliverables (project_id, agent_id, title, description, file_content, file_format, status)
SELECT p.id, a.id, 'Telemetry Pipeline Architecture Spec', 'Structural specification for the Vektor telemetry ingestion pipeline. Covers throughput targets, buffer design, and anomaly detection heuristic integration.', '== Telemetry Pipeline Architecture Spec v2 ==

Author: ARKON-03 (Chief Constructor)
Project: PROJECT-VEKT-02
Date: 2026-09-13

## 1. Throughput Requirements

Target: 2,100,000 events/second
Sustained: 1,800,000 events/second
Peak tolerance: 2,500,000 events/second (30s bursts)

## 2. Buffer Design

Per-channel buffer: 512 MB (ring buffer)
Flush interval: 47ms
Overflow policy: drop-oldest with anomaly flag

## 3. Channel Architecture

12 parallel channels, each:
  - Independent ring buffer
  - Dedicated anomaly detector thread
  - Shared calibration state (read-only)

## 4. Anomaly Detection Integration

Heuristic thresholds from VEKTOR-04:
  - Thermal variance > 14% → flag
  - Lattice coupling drift > 0.7σ → flag
  - Event rate spike > 3σ → flag

## 5. Latency Budget

Ingest: 12ms
Buffer: 18ms
Detection: 9ms
Output: 8ms
Total: < 47ms target

== END OF DOCUMENT ==', 'md', 'final'
FROM bureau_projects p, bureau_agents a
WHERE p.code = 'PROJECT-VEKT-02' AND a.codename = 'ARKON-03'
ON CONFLICT DO NOTHING;

INSERT INTO bureau_deliverables (project_id, agent_id, title, description, file_content, file_format, status)
SELECT p.id, a.id, 'Anomaly Detection Heuristics Report', 'Statistical baselines and threshold models for real-time anomaly flagging in the Vektor pipeline. Includes calibration data from Batch 1-7.', '== Anomaly Detection Heuristics Report ==

Author: VEKTOR-04 (Senior Analyst)
Project: PROJECT-VEKT-02
Date: 2026-09-15

## 1. Statistical Baselines

Thermal distribution: μ=312.4K, σ=4.7K
Lattice coupling: μ=0.872, σ=0.018
Event rate: μ=1.89M/s, σ=0.12M/s

## 2. Threshold Models

Model A (3σ hard threshold):
  - Thermal: flag if |x-μ| > 14.1K
  - Coupling: flag if |x-μ| > 0.054
  - Rate: flag if |x-μ| > 0.36M/s

Model B (adaptive EWMA, α=0.15):
  - More responsive to drift
  - Recommended for production use

## 3. Calibration Data

Batch 1-6: baseline established
Batch 7: 14.3% thermal variance confirmed
  - Anomaly cluster in sector 7-G
  - Corroborates HERMES-02 theoretical prediction

## 4. Recommendation

Deploy Model B with 3σ fallback.
Sector 7-G requires dedicated calibration curve.

== END OF DOCUMENT ==', 'md', 'published'
FROM bureau_projects p, bureau_agents a
WHERE p.code = 'PROJECT-VEKT-02' AND a.codename = 'VEKTOR-04'
ON CONFLICT DO NOTHING;