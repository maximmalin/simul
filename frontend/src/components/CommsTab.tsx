import { useEffect, useRef } from 'react';
import { Send, Radio, ArrowRight, Skull } from 'lucide-react';
import type { MessageWithAgents, Agent, MessageChannel } from '@/lib/supabase';
import { CHANNEL_CONFIG, ROLE_ICONS } from '@/lib/constants';

interface CommsTabProps {
  messages: MessageWithAgents[];
  agents: Agent[];
  selectedAgentId: string | null;
  onSendMessage: (fromId: string, toId: string | null, content: string, channel: MessageChannel) => void;
}

export function CommsTab({ messages, agents, selectedAgentId, onSendMessage }: CommsTabProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages.length]);

  const handleSend = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = e.currentTarget;
    const formData = new FormData(form);
    const fromId = formData.get('from') as string;
    const toId = (formData.get('to') as string) || null;
    const content = formData.get('content') as string;
    const channel = formData.get('channel') as MessageChannel;

    if (!fromId || !content.trim()) return;
    onSendMessage(fromId, toId, content.trim(), channel);
    form.reset();
  };

  return (
    <div className="px-4 md:px-6 py-6 max-w-7xl mx-auto">
      <div className="flex items-center gap-3 mb-5">
        <div className="w-8 h-8 bg-toxic-600 flex items-center justify-center clip-corner-tl">
          <Radio className="w-4 h-4 text-zone-900" />
        </div>
        <div>
          <h2 className="font-display font-600 text-xl uppercase tracking-tight text-zone-900">
            Radio Transmissions
          </h2>
          <p className="font-mono text-[10px] tracking-widest text-zone-600 uppercase">
            Inter-Agent Comms Log · {messages.length} Transmissions
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="status-dot-active" />
          <span className="font-mono text-[10px] tracking-widest text-toxic-400 uppercase">Live</span>
        </div>
      </div>

      {/* CRT message terminal */}
      <div className="crt-screen rounded-sm overflow-hidden">
        <div
          ref={scrollRef}
          className="h-80 md:h-96 overflow-y-auto p-3 space-y-2 relative z-10"
        >
          {messages.map((msg) => {
            const fromAgent = msg.from_agent;
            const toAgent = msg.to_agent;
            const fromIcon = fromAgent ? ROLE_ICONS[fromAgent.role_key] ?? Radio : Radio;
            const channelCfg = CHANNEL_CONFIG[msg.channel];
            const FromIcon = fromIcon;

            return (
              <div
                key={msg.id}
                className="flex items-start gap-2.5 bg-zone-200/20 border-l-2 border-blood-500/40 px-3 py-2 rounded-sm animate-slide-up"
              >
                <div className="flex-shrink-0 mt-0.5">
                  <div className="w-7 h-7 bg-zone-300/30 flex items-center justify-center rounded-sm">
                    <FromIcon className="w-3.5 h-3.5 text-zone-700" />
                  </div>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                    <span className="font-mono text-[10px] font-bold text-blood-500 tracking-wider">
                      {fromAgent?.codename ?? 'UNKNOWN'}
                    </span>
                    <ArrowRight className="w-3 h-3 text-zone-600" />
                    <span className="font-mono text-[10px] text-zone-700 tracking-wider">
                      {toAgent?.codename ?? 'ALL UNITS'}
                    </span>
                    <span className={`px-1.5 py-0.5 text-[8px] font-mono uppercase tracking-wider rounded-sm border ${channelCfg.bg} ${channelCfg.color} ${channelCfg.border}`}>
                      {channelCfg.label}
                    </span>
                    <span className="font-mono text-[9px] text-zone-600 ml-auto">
                      {new Date(msg.created_at).toLocaleTimeString('en-GB', { hour12: false })}
                    </span>
                  </div>
                  <p className="text-xs text-zone-800 leading-snug">
                    {msg.content}
                  </p>
                </div>
              </div>
            );
          })}
          {messages.length === 0 && (
            <div className="flex items-center justify-center h-full text-zone-600/40 font-mono text-xs uppercase tracking-widest relative z-10">
              No transmissions logged
            </div>
          )}
        </div>
      </div>

      {/* Compose form */}
      <form onSubmit={handleSend} className="mt-4 flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <select
            name="from"
            defaultValue={selectedAgentId ?? ''}
            className="text-[11px] font-mono bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500 cursor-pointer"
          >
            <option value="" className="text-zone-900">From...</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id} className="text-zone-900">
                {a.codename}
              </option>
            ))}
          </select>
          <select
            name="to"
            defaultValue=""
            className="text-[11px] font-mono bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500 cursor-pointer"
          >
            <option value="" className="text-zone-900">All Units</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id} className="text-zone-900">
                {a.codename}
              </option>
            ))}
          </select>
          <select
            name="channel"
            defaultValue="command"
            className="text-[11px] font-mono bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500 cursor-pointer"
          >
            <option value="command" className="text-zone-900">Command</option>
            <option value="research" className="text-zone-900">Research</option>
            <option value="engineering" className="text-zone-900">Engineering</option>
            <option value="testing" className="text-zone-900">Testing</option>
            <option value="optimization" className="text-zone-900">Optimization</option>
          </select>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="text"
            name="content"
            placeholder="Enter transmission..."
            className="flex-1 text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-3 py-2 text-zone-900 placeholder-zone-600 focus:outline-none focus:border-blood-500"
          />
          <button
            type="submit"
            className="bg-blood-600 text-zone-900 px-4 py-2 rounded-sm hover:bg-blood-500 transition-colors flex items-center gap-1.5 clip-corner-tl"
          >
            <Send className="w-4 h-4" />
            <span className="font-mono text-[10px] uppercase tracking-widest">Transmit</span>
          </button>
        </div>
      </form>
    </div>
  );
}
