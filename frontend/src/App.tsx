import { useEffect, useState, useCallback } from 'react';
import { supabase, SUPABASE_CONFIGURED } from '@/lib/supabase';
import type { Agent, Project, Task, MessageWithAgents, MessageChannel, TaskStatus, Priority } from '@/lib/supabase';
import { ZoneHeader } from '@/components/ZoneHeader';
import { TabNav, type TabKey } from '@/components/TabNav';
import { OverviewTab } from '@/components/OverviewTab';
import { KrystalTab } from '@/components/KrystalTab';
import { OrderTab } from '@/components/OrderTab';
import { PersonnelTab } from '@/components/PersonnelTab';
import { ProjectsTab } from '@/components/ProjectsTab';
import { CommsTab } from '@/components/CommsTab';
import { ChatTab } from '@/components/ChatTab';
import { DeliverablesTab } from '@/components/DeliverablesTab';
import { WarehouseTab } from '@/components/WarehouseTab';
import { FlowEditorTab } from '@/components/FlowEditorTab';
import { DevDeptTab } from '@/components/DevDeptTab';

function App() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [messages, setMessages] = useState<MessageWithAgents[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<TabKey>('overview');

  const fetchAll = useCallback(async () => {
    // Supabase может быть не настроен — тогда вкладки бюро пустые,
    // но локальные (Krystal / агентный мост) продолжают работать.
    if (!SUPABASE_CONFIGURED) {
      setLoading(false);
      return;
    }
    try {
      const [agentsRes, projectsRes, tasksRes, messagesRes] = await Promise.all([
        supabase.from('bureau_agents').select('*').order('created_at'),
        supabase.from('bureau_projects').select('*').order('created_at'),
        supabase.from('bureau_tasks').select('*').order('sort_order'),
        supabase
          .from('bureau_messages')
          .select(`
            *,
            from_agent:bureau_agents!from_agent_id(id, codename, role_key),
            to_agent:bureau_agents!to_agent_id(id, codename, role_key)
          `)
          .order('created_at', { ascending: true })
          .limit(50),
      ]);

      setAgents(agentsRes.data ?? []);
      setProjects(projectsRes.data ?? []);
      setTasks(tasksRes.data ?? []);
      setMessages((messagesRes.data ?? []) as unknown as MessageWithAgents[]);
    } catch (err) {
      console.warn('Supabase недоступен, бюро-вкладки пустые:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const handleCreateTask = useCallback(async (task: {
    project_id: string;
    agent_id: string | null;
    title: string;
    directive: string;
    priority: Priority;
  }) => {
    const maxOrder = tasks
      .filter((t) => t.project_id === task.project_id)
      .reduce((max, t) => Math.max(max, t.sort_order), 0);
    const { data, error } = await supabase
      .from('bureau_tasks')
      .insert({
        project_id: task.project_id,
        agent_id: task.agent_id,
        title: task.title,
        directive: task.directive,
        priority: task.priority,
        status: 'pending',
        sort_order: maxOrder + 1,
      })
      .select('*')
      .single();
    if (!error && data) {
      setTasks((prev) => [...prev, data as Task]);
    }
  }, [tasks]);

  const handleUpdateTaskStatus = useCallback(async (taskId: string, status: TaskStatus) => {
    setTasks((prev) => prev.map((t) => (t.id === taskId ? { ...t, status } : t)));
    await supabase.from('bureau_tasks').update({ status }).eq('id', taskId);
  }, []);

  const handleReassignTask = useCallback(async (taskId: string, agentId: string | null) => {
    setTasks((prev) => prev.map((t) => (t.id === taskId ? { ...t, agent_id: agentId } : t)));
    await supabase.from('bureau_tasks').update({ agent_id: agentId }).eq('id', taskId);
  }, []);

  const handleSendMessage = useCallback(async (
    fromId: string,
    toId: string | null,
    content: string,
    channel: MessageChannel
  ) => {
    const { data, error } = await supabase
      .from('bureau_messages')
      .insert({
        from_agent_id: fromId,
        to_agent_id: toId,
        content,
        channel,
      })
      .select(`
        *,
        from_agent:bureau_agents!from_agent_id(id, codename, role_key),
        to_agent:bureau_agents!to_agent_id(id, codename, role_key)
      `)
      .single();

    if (!error && data) {
      const typed = data as unknown as MessageWithAgents;
      setMessages((prev) => [...prev, typed]);
    }
  }, []);

  const totalCycles = agents.reduce((sum, a) => sum + a.cycles_completed, 0);
  const activeProjects = projects.filter((p) => p.status === 'active').length;

  if (loading) {
    return (
      <div className="min-h-screen bg-zone flex items-center justify-center noise-overlay">
        <div className="text-center relative z-10">
          <div className="inline-block w-12 h-12 border-4 border-blood-600 border-t-transparent rounded-full animate-spin mb-4" />
          <p className="font-mono text-xs tracking-widest text-zone-600 uppercase">
            Initializing Bureau Systems...
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-zone noise-overlay">
      <ZoneHeader
        agentCount={agents.length}
        activeProjects={activeProjects}
        totalCycles={totalCycles}
      />

      <TabNav activeTab={activeTab} onTabChange={setActiveTab} />

      {!SUPABASE_CONFIGURED && (
        <div className="max-w-7xl mx-auto px-4 md:px-6 pt-4">
          <div className="flex items-start gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <span className="font-mono text-[10px] uppercase tracking-widest mt-0.5 shrink-0">
              Supabase
            </span>
            <span>
              Не заданы <code className="font-mono">VITE_SUPABASE_URL</code> и{' '}
              <code className="font-mono">VITE_SUPABASE_ANON_KEY</code> — вкладки
              бюро (Overview, Personnel, Projects, Comms) останутся пустыми.
              Вкладка <strong>Krystal</strong> работает от локального агентного
              моста и доступна полностью.
            </span>
          </div>
        </div>
      )}

      <main className="relative z-10">
        {activeTab === 'overview' && (
          <OverviewTab agents={agents} projects={projects} tasks={tasks} />
        )}

        {activeTab === 'order' && (
          <OrderTab />
        )}

        {activeTab === 'krystal' && (
          <KrystalTab />
        )}
        {activeTab === 'personnel' && (
          <PersonnelTab
            agents={agents}
            tasks={tasks}
            selectedAgentId={selectedAgentId}
            onSelectAgent={setSelectedAgentId}
          />
        )}
        {activeTab === 'projects' && (
          <ProjectsTab
            projects={projects}
            tasks={tasks}
            agents={agents}
            selectedAgentId={selectedAgentId}
            onCreateTask={handleCreateTask}
            onUpdateTaskStatus={handleUpdateTaskStatus}
            onReassignTask={handleReassignTask}
          />
        )}
        {activeTab === 'comms' && (
          <CommsTab
            messages={messages}
            agents={agents}
            selectedAgentId={selectedAgentId}
            onSendMessage={handleSendMessage}
          />
        )}
        {activeTab === 'chat' && (
          <ChatTab agents={agents} projects={projects} />
        )}
        {activeTab === 'deliverables' && (
          <DeliverablesTab />
        )}
        {activeTab === 'warehouse' && (
          <WarehouseTab />
        )}
        {activeTab === 'flow' && (
          <FlowEditorTab agents={agents} projects={projects} />
        )}
        {activeTab === 'devdept' && (
          <DevDeptTab />
        )}
      </main>

      {/* Footer */}
      <footer className="bg-zone-50 border-t border-zone-300/30 py-5 px-6 md:px-10 noise-overlay">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row items-center justify-between gap-3 relative z-10">
          <div className="font-mono text-[10px] tracking-widest uppercase text-zone-600">
            Bureau No. 7 · Seren + Hermes
          </div>
          <div className="font-mono text-[10px] tracking-widest uppercase text-zone-600">
            Zone-Class Engineering · Restricted
          </div>
          <div className="font-mono text-[10px] tracking-widest uppercase text-blood-500">
            ▲ Пролетарии всех стран, соединяйтесь!
          </div>
        </div>
      </footer>
    </div>
  );
}

export default App;
