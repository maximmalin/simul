/**
 * Krystal Radio — клиент агентного моста (backend/agent_bridge.py).
 *
 * Весь интерфейс общается с проектом через этот модуль: запуск make-целей,
 * чтение артефактов из out/, метрики приёмника, список агентов и тесты.
 *
 * В dev режиме Vite проксирует /api и /out на мост (см. vite.config.ts),
 * поэтому здесь используются относительные пути — CORS не возникает.
 */

const BASE = (import.meta.env.VITE_BRIDGE_URL as string | undefined) ?? '';

export interface Artifact {
  name: string;
  path: string;
  rel: string;
  size: number;
  size_h: string;
  ext: string;
  mime: string;
  modified: string;
  url: string;
}

export interface Health {
  ok: boolean;
  root: string;
  time: string;
  artifacts: number;
  has_page: boolean;
  has_waterfall: boolean;
  has_glb: boolean;
  jobs: number;
}

export interface AgentDef {
  id: string;
  title: string;
  goal: string;
  files: string;
  command: string;
  cad?: string;
  outputs?: string[];
}

export interface MakeTarget {
  target: string;
  desc: string;
}

export interface ScriptTarget {
  script: string;
  path: string;
  desc: string;
}

export interface Job {
  id: string;
  target: string;
  status: 'queued' | 'running' | 'done' | 'failed';
  started_at: string;
  finished_at: string | null;
  exit_code: number | null;
  log: string[];
  log_offset?: number;
}

export interface ChannelRange {
  key: string;
  title: string;
  f_mhz: number;
  g_rx_dbi: number;
  range_free_km: number;
  range_real_km: number;
}

export interface Metrics {
  link_budget: {
    noise_floor_dbm: number;
    mds_dbm: number;
    eirp_dbm: number;
    bw_hz: number;
    nf_db: number;
    channels: ChannelRange[];
  };
  direction: {
    theta_est_deg: number;
    sigma_theta_deg: number;
    fov_deg: number;
    lambda_mm: number;
    baseline_mm: number;
    backend: string;
  };
  thermal: {
    tj_c: number | null;
    tj_limit_c: number | null;
    tmax_fem_c: number | null;
    devices: { ref: string; p_w: number; tj_c: number; tj_max_c: number; margin_c: number; pass: boolean }[];
  };
  reliability: { mtbf_h: number | null; req_min_h: number; pass: boolean | null };
  receiver: Record<string, unknown>;
  emc: { test_file: string; expected: number; note: string };
}

export interface TestResult {
  ok: boolean;
  exit_code?: number;
  passed?: number;
  failed?: number;
  tail?: string[];
  error?: string;
}

// --- агентный рантайм ------------------------------------------------------

export type ChainState = 'done' | 'stale' | 'running' | 'missing';

export interface ChainStage {
  n: number;
  key: string;
  label: string;
  sub: string;
  target: string;
  state: ChainState;
  files: { path: string; size: number; modified: string }[];
  has_all: boolean;
  job: { id: string; agent_id: string | null; started_at: string } | null;
}

export interface Chain {
  stages: ChainStage[];
  total: number;
  done: number;
  running: number;
  progress: number;
  updated: string;
}

export interface RuntimeAgent {
  id: string;
  codename: string;
  title: string;
  goal: string;
  files: string;
  command: string;
  role_key: string;
  role_title: string;
  designation: string;
  channel: string;
}

export interface KrProject {
  id: string;
  code: string;
  title: string;
  objective: string;
  status: string;
  priority: string;
  progress: number;
  created_at: string;
  tasks_total?: number;
  tasks_done?: number;
}

export interface ZoneTelemetry {
  status: 'nominal' | 'degraded' | 'critical';
  status_label: string;
  radiation_c: number | null;
  radiation_display: string;
  anomalies_count: number;
  anomalies: { key: string; label: string; state: ChainState }[];
  network_pct: number;
  network_display: string;
  chain_progress_pct: number;
  chain_done: number;
  chain_total: number;
  has_page: boolean;
  has_waterfall: boolean;
  has_glb: boolean;
}

/** Полный снимок для дашборда: один опрос вместо шести запросов. */
export interface Snapshot {
  health: Health;
  chain: Chain;
  zone: ZoneTelemetry;
  metrics: Metrics;
  projects: { source: string; count: number; projects: KrProject[] };
  specs: Record<string, { product: string; specs: { label: string; value: string }[] }>;
  tests: TestResult | null;
  jobs: { id: string; target: string; status: string; agent_id: string | null; exit_code: number | null; started_at: string }[];
  updated: string;
}

export interface KrConfig {
  dev_roles: { key: string; label: string; designation: string }[];
  languages: string[];
  frameworks: string[];
  warehouse: { categories: string[]; manufacturers: string[]; mcus: string[] };
  enums: Record<string, string[]>;
  product_specs: Record<string, { product: string; specs: { label: string; value: string }[] }>;
  aliases: Record<string, string>;
}

export const getSnapshot = () => req<Snapshot>('/api/snapshot');
export const getZone = () => req<ZoneTelemetry>('/api/zone');
export const getConfig = () => req<KrConfig>('/api/config');
export const getSpecs = (code: string) =>
  req<{ code: string; product: string; specs: { label: string; value: string }[] }>(
    `/api/specs/${encodeURIComponent(code)}`,
  );

export const getChain = () => req<Chain>('/api/chain');

export const getProjects = () =>
  req<{ source: string; count: number; projects: KrProject[] }>('/api/projects');

export const getRuntimeAgents = () =>
  req<{ ok: boolean; count: number; agents: RuntimeAgent[] }>('/api/runtime/agents');

export const getRuntimeContext = () => req<{ context: string }>('/api/runtime/context');

export const createProject = (p: {
  code: string;
  title: string;
  objective?: string;
  priority?: string;
}) =>
  req<{ ok?: boolean; project?: KrProject; error?: string }>('/api/projects', {
    method: 'POST',
    body: JSON.stringify(p),
  });

/** Запустить make-цель от имени агента. */
export const runAsAgent = (agentId: string, target: string) =>
  req<Job>('/api/agent/run', {
    method: 'POST',
    body: JSON.stringify({ agent_id: agentId, target }),
  });

/** Спросить рантайм (маршрутизация к агенту + LLM-ответ). */
export const ask = (prompt: string) =>
  req<{
    agent_id: string;
    agent_title: string;
    response: string;
    task_created: boolean;
  }>('/api/ask', { method: 'POST', body: JSON.stringify({ prompt }) });

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`${res.status} ${res.statusText} ${text.slice(0, 200)}`);
  }
  return (await res.json()) as T;
}

// --- чтение ----------------------------------------------------------------

export const getHealth = () => req<Health>('/api/health');

export async function getArtifacts(ext?: string[]): Promise<Artifact[]> {
  const q = ext && ext.length ? `?ext=${ext.join(',')}` : '';
  const d = await req<{ count: number; artifacts: Artifact[] }>(
    `/api/artifacts${q}`,
  );
  return d.artifacts;
}

export const getAgents = () => req<{ count: number; agents: AgentDef[] }>('/api/agents');

export const getAgent = (id: string) => req<AgentDef>(`/api/agent/${encodeURIComponent(id)}`);

export const getTargets = () =>
  req<{ targets: MakeTarget[]; scripts: ScriptTarget[] }>('/api/targets');

export const getMetrics = () => req<Metrics>('/api/metrics');

export const getTests = () => req<TestResult>('/api/tests');

export const getJobs = () => req<{ jobs: Job[] }>('/api/jobs');

/** URL артефакта для <img>, <video>, three.js GLTFLoader. */
export const artifactUrl = (relPath: string) =>
  `${BASE}/api/artifact/${relPath.split('/').map(encodeURIComponent).join('/')}`;

// --- запуск ----------------------------------------------------------------

export const startBuild = (target: string) =>
  req<Job>('/api/build', { method: 'POST', body: JSON.stringify({ target }) });

export const startScript = (script: string, args: string[] = []) =>
  req<Job>('/api/run', { method: 'POST', body: JSON.stringify({ script, args }) });

export const getJob = (id: string, since = 0) =>
  req<Job>(`/api/build/${id}?since=${since}`);

export const getPrompt = (agentId: string) =>
  req<{ agent_id: string; prompt: string }>('/api/prompt', {
    method: 'POST',
    body: JSON.stringify({ agent_id: agentId }),
  });

// --- подписка на лог джобы -------------------------------------------------

/**
 * Опрос лога джобы до завершения. onChunk получает новые строки.
 * Возвращает функцию отмены.
 */
export function watchJob(
  id: string,
  onChunk: (lines: string[], job: Job) => void,
  onDone?: (job: Job) => void,
  intervalMs = 900,
): () => void {
  let offset = 0;
  let cancelled = false;
  const timer = setInterval(async () => {
    if (cancelled) return;
    try {
      const job = await getJob(id, offset);
      if (job.log?.length) {
        offset += job.log.length;
        onChunk(job.log, job);
      }
      if (job.status === 'done' || job.status === 'failed') {
        clearInterval(timer);
        onDone?.(job);
      }
    } catch {
      clearInterval(timer);
    }
  }, intervalMs);
  return () => {
    cancelled = true;
    clearInterval(timer);
  };
}