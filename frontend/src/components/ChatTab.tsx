import { useEffect, useRef, useState } from 'react';
import { Send, Radio, ArrowRight, Skull, Brain, DraftingCompass, ScanLine, FlaskRound, Gauge, Loader2, CheckCircle2, FolderKanban, type LucideIcon } from 'lucide-react';
import { supabase } from '@/lib/supabase';
import type { Agent, Project, ChatRequestWithAgent } from '@/lib/supabase';
import { ROLE_ICONS } from '@/lib/constants';

interface ChatTabProps {
  agents: Agent[];
  projects: Project[];
}

interface ChatMessage {
  id: string;
  role: 'user' | 'agent';
  content: string;
  agentCodename?: string;
  agentCallsign?: string;
  agentRoleKey?: string;
  taskCreated?: boolean;
  timestamp: string;
}

const ROLE_ICON_FALLBACK: Record<string, LucideIcon> = {
  chief: Skull,
  theorist: Brain,
  designer: DraftingCompass,
  analyst: ScanLine,
  tester: FlaskRound,
  optimizer: Gauge,
};

export function ChatTab({ agents, projects }: ChatTabProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [selectedProject, setSelectedProject] = useState('');
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchHistory();
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const fetchHistory = async () => {
    const { data } = await supabase
      .from('bureau_chat_requests')
      .select(`
        *,
        response_agent:bureau_agents!response_agent_id(id, codename, role_key, role_title, zone_callsign)
      `)
      .order('created_at', { ascending: true })
      .limit(50);

    if (data) {
      const typed = data as unknown as ChatRequestWithAgent[];
      const mapped: ChatMessage[] = typed.map((req) => ({
        id: req.id,
        role: 'user',
        content: req.content,
        timestamp: req.created_at,
      }));
      typed.forEach((req) => {
        if (req.response_text && req.response_agent) {
          mapped.push({
            id: `${req.id}-response`,
            role: 'agent',
            content: req.response_text,
            agentCodename: req.response_agent.codename,
            agentCallsign: req.response_agent.zone_callsign,
            agentRoleKey: req.response_agent.role_key,
            taskCreated: true,
            timestamp: req.responded_at ?? req.created_at,
          });
        }
      });
      mapped.sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
      setMessages(mapped);
    }
  };

  const handleSend = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || sending) return;

    const userMsg: ChatMessage = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: input.trim(),
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setSending(true);

    try {
      const apiUrl = `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/process-chat`;
      const headers = {
        Authorization: `Bearer ${import.meta.env.VITE_SUPABASE_ANON_KEY}`,
        'Content-Type': 'application/json',
      };
      const response = await fetch(apiUrl, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          content: input.trim(),
          project_id: selectedProject || null,
        }),
      });

      if (!response.ok) {
        throw new Error(`Request failed (${response.status})`);
      }

      const data = await response.json();

      const agentMsg: ChatMessage = {
        id: `agent-${Date.now()}`,
        role: 'agent',
        content: data.response,
        agentCodename: data.agent?.codename,
        agentCallsign: data.agent?.zone_callsign,
        agentRoleKey: data.agent?.role_key,
        taskCreated: data.task_created,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, agentMsg]);
    } catch {
      const errMsg: ChatMessage = {
        id: `err-${Date.now()}`,
        role: 'agent',
        content: 'Transmission failed. The Zone interferes with all communications. Retry.',
        agentCodename: 'SYSTEM',
        agentCallsign: 'Error',
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setInput('');
      setSending(false);
    }
  };

  return (
    <div className="px-4 md:px-6 py-6 max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-5">
        <div className="w-8 h-8 bg-blood-600 flex items-center justify-center clip-corner-tl">
          <Radio className="w-4 h-4 text-zone-900" />
        </div>
        <div>
          <h2 className="font-display font-600 text-xl uppercase tracking-tight text-zone-900">
            Bureau Command Chat
          </h2>
          <p className="font-mono text-[10px] tracking-widest text-zone-600 uppercase">
            Operator → Bureau Channel · Dispatch Tech Tasks
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="status-dot-active" />
          <span className="font-mono text-[10px] tracking-widest text-toxic-400 uppercase">Connected</span>
        </div>
      </div>

      {/* Chat terminal */}
      <div className="crt-screen rounded-sm overflow-hidden mb-4">
        <div ref={scrollRef} className="h-80 md:h-[28rem] overflow-y-auto p-4 space-y-3 relative z-10">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-zone-600/40 relative z-10">
              <Radio className="w-8 h-8 mb-2" />
              <p className="font-mono text-xs uppercase tracking-widest">
                Awaiting transmission from operator
              </p>
              <p className="font-mono text-[10px] text-zone-600/30 mt-1">
                Send a directive to dispatch a tech task to the bureau
              </p>
            </div>
          )}

          {messages.map((msg) => {
            if (msg.role === 'user') {
              return (
                <div key={msg.id} className="flex justify-end animate-slide-up">
                  <div className="max-w-[80%] bg-blood-600/20 border border-blood-500/30 rounded-sm px-3 py-2">
                    <div className="flex items-center gap-1.5 mb-1">
                      <span className="font-mono text-[8px] tracking-widest text-blood-500 uppercase font-bold">
                        Operator
                      </span>
                      <span className="font-mono text-[8px] text-zone-600">
                        {new Date(msg.timestamp).toLocaleTimeString('en-GB', { hour12: false })}
                      </span>
                    </div>
                    <p className="text-xs text-zone-900 leading-snug">{msg.content}</p>
                  </div>
                </div>
              );
            }

            const Icon = msg.agentRoleKey ? ROLE_ICON_FALLBACK[msg.agentRoleKey] ?? Radio : Radio;
            return (
              <div key={msg.id} className="flex items-start gap-2.5 animate-slide-up">
                <div className="flex-shrink-0 w-8 h-8 bg-zone-300/30 flex items-center justify-center rounded-sm border border-zone-400/20">
                  <Icon className="w-4 h-4 text-zone-700" />
                </div>
                <div className="max-w-[80%] bg-zone-200/30 border-l-2 border-toxic-500/40 rounded-sm px-3 py-2">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-mono text-[9px] font-bold text-toxic-400 tracking-wider">
                      {msg.agentCodename ?? 'BUREAU'}
                    </span>
                    {msg.agentCallsign && (
                      <span className="font-mono text-[8px] text-zone-600 tracking-wider">
                        · {msg.agentCallsign}
                      </span>
                    )}
                    <span className="font-mono text-[8px] text-zone-600 ml-auto">
                      {new Date(msg.timestamp).toLocaleTimeString('en-GB', { hour12: false })}
                    </span>
                  </div>
                  <p className="text-xs text-zone-800 leading-snug">{msg.content}</p>
                  {msg.taskCreated && (
                    <div className="flex items-center gap-1.5 mt-2 pt-1.5 border-t border-zone-400/15">
                      <CheckCircle2 className="w-3 h-3 text-toxic-400" />
                      <span className="font-mono text-[9px] text-toxic-400 uppercase tracking-wider">
                        Tech task created — check Projects tab
                      </span>
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {sending && (
            <div className="flex items-center gap-2.5 animate-slide-up">
              <div className="flex-shrink-0 w-8 h-8 bg-zone-300/20 flex items-center justify-center rounded-sm border border-zone-400/20">
                <Loader2 className="w-4 h-4 text-zone-600 animate-spin" />
              </div>
              <div className="bg-zone-200/20 border-l-2 border-zone-400/20 rounded-sm px-3 py-2">
                <span className="font-mono text-[10px] text-zone-600 uppercase tracking-wider animate-blink">
                  Bureau processing directive...
                </span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Input form */}
      <form onSubmit={handleSend} className="space-y-3">
        <div className="flex items-center gap-2">
          <FolderKanban className="w-4 h-4 text-zone-600 flex-shrink-0" />
          <select
            value={selectedProject}
            onChange={(e) => setSelectedProject(e.target.value)}
            className="text-[11px] font-mono bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500 cursor-pointer flex-1"
          >
            <option value="">Auto-assign to active project</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.code} — {p.title}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Enter directive for the bureau..."
            disabled={sending}
            className="flex-1 text-sm bg-zone-200 border border-zone-400/20 rounded-sm px-3 py-2.5 text-zone-900 placeholder-zone-600 focus:outline-none focus:border-blood-500 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!input.trim() || sending}
            className="bg-blood-600 text-zone-900 px-4 py-2.5 rounded-sm hover:bg-blood-500 transition-colors flex items-center gap-1.5 clip-corner-tl disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <Send className="w-4 h-4" />
            <span className="font-mono text-[10px] uppercase tracking-widest">Dispatch</span>
          </button>
        </div>
      </form>
    </div>
  );
}
