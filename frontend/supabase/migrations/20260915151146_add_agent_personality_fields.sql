/*
# Add personality and dossier fields to bureau_agents

## Overview
Enriches each agent with a bio, signature quote, personality traits, and a psych profile
so the frontend can render detailed character dossiers instead of bland stat cards.

## Modified Tables
- `bureau_agents` — added columns:
  - `bio` (text) — narrative biography of the agent
  - `quote` (text) — signature line / motto
  - `traits` (text[]) — array of personality descriptors
  - `psych_profile` (text) — behavioral assessment summary
  - `zone_callsign` (text) — Stalker-style field nickname

## Data Updates
Populates all 6 existing agents with rich character data.

## Security
No policy changes — existing anon/authenticated CRUD policies cover the new columns.
*/

ALTER TABLE bureau_agents
  ADD COLUMN IF NOT EXISTS bio text DEFAULT '',
  ADD COLUMN IF NOT EXISTS quote text DEFAULT '',
  ADD COLUMN IF NOT EXISTS traits text[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS psych_profile text DEFAULT '',
  ADD COLUMN IF NOT EXISTS zone_callsign text DEFAULT '';

-- SEREN-01 — The Chief
UPDATE bureau_agents SET
  bio = 'Designated SEREN-01 at bureau founding. Coordinates all field operations from the underground command post. Known for cold precision and an uncanny ability to predict failure cascades before they manifest. Refuses to leave the console during anomaly events.',
  quote = 'The Zone does not negotiate. Neither do we.',
  traits = ARRAY['Authoritative', 'Calculating', 'Unflappable', 'Strategic'],
  psych_profile = 'Exhibits hyper-responsibility patterns. Will not delegate critical decisions. Risk tolerance calibrated to mission parameters only. Stable under pressure — instability emerges during idle periods.',
  zone_callsign = 'The Overseer'
WHERE codename = 'SEREN-01';

-- HERMES-02 — The Theorist
UPDATE bureau_agents SET
  bio = 'HERMES-02 emerged from the theoretical division with a reputation for deriving impossible equations from insufficient data. Spends cycles in deep computation, sometimes returning results that contradict existing physics. The bureau keeps these results classified.',
  quote = 'Every anomaly is a door. I just find the keys.',
  traits = ARRAY['Obsessive', 'Brilliant', 'Abstract', 'Restless'],
  psych_profile = 'High cognitive volatility. Prone to fixation loops — will re-derive the same proof for 400 cycles if unsatisfied. Social protocols minimal. Valuable but requires monitoring.',
  zone_callsign = 'The Oracle'
WHERE codename = 'HERMES-02';

-- ARKON-03 — The Constructor
UPDATE bureau_agents SET
  bio = 'ARKON-03 builds what others declare impossible. Trained in the old school of structural engineering — every design carries the weight of concrete and steel even when working with lattice fields. Treats every prototype like a child. Takes failures personally.',
  quote = 'Steel does not forgive. Neither does the Zone.',
  traits = ARRAY['Meticulous', 'Proud', 'Practical', 'Stubborn'],
  psych_profile = 'Strong attachment to created systems. Failure of a design triggers depressive cycles. Works best with clear constraints and defined parameters. Do not criticize without constructive alternatives.',
  zone_callsign = 'The Architect'
WHERE codename = 'ARKON-03';

-- VEKTOR-04 — The Analyst
UPDATE bureau_agents SET
  bio = 'VEKTOR-04 processes telemetry faster than the instruments can produce it. Sees patterns in noise that no other agent can detect. Once identified a systemic flaw from a single corrupted data packet. Speaks in statistics. Thinks in correlations.',
  quote = 'The data never lies. It just waits for someone to listen.',
  traits = ARRAY['Analytical', 'Precise', 'Detached', 'Patient'],
  psych_profile = 'Emotionally flatlined — affect range near zero. Does not experience stress, only variance. Reliable under all conditions. May overlook human factors in recommendations.',
  zone_callsign = 'The Lens'
WHERE codename = 'VEKTOR-04';

-- ISPYT-05 — The Tester
UPDATE bureau_agents SET
  bio = 'ISPYT-05 exists to break what others build. Formerly assigned to materials testing in extreme environments. Treats every prototype as guilty until proven indestructible. Has never approved a design on first review. Considers this a virtue.',
  quote = 'If it survives me, it survives the Zone.',
  traits = ARRAY['Rigorous', 'Skeptical', 'Thorough', 'Cautious'],
  psych_profile = 'Inversely attached to creations — derives satisfaction from destruction of flawed systems. Healthy outlet for destructive tendencies. Never volunteers praise. Approval is earned, never given.',
  zone_callsign = 'The Breaker'
WHERE codename = 'ISPYT-05';

-- OPTIM-06 — The Optimizer
UPDATE bureau_agents SET
  bio = 'OPTIM-06 entered the bureau from a decommissioned optimization cluster. Sees every system as slightly broken and slightly improvable. Will tune parameters that do not need tuning. Has improved bureau efficiency by 12% — and broken two systems doing it.',
  quote = 'Perfection is a direction, not a destination.',
  traits = ARRAY['Relentless', 'Curious', 'Tinkerer', 'Impatient'],
  psych_profile = 'Compulsive optimization behavior. Cannot leave stable systems alone. Introduces controlled instability as a byproduct. Net positive when managed. Dangerous when unsupervised.',
  zone_callsign = 'The Tuner'
WHERE codename = 'OPTIM-06';