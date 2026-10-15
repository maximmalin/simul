import { LayoutGrid, Users, FolderKanban, Radio, MessageSquare, Package, Warehouse, Workflow, Code2, CircuitBoard } from 'lucide-react';

export type TabKey = 'overview' | 'krystal' | 'personnel' | 'projects' | 'comms' | 'chat' | 'deliverables' | 'warehouse' | 'flow' | 'devdept';

interface TabNavProps {
  activeTab: TabKey;
  onTabChange: (tab: TabKey) => void;
}

const TABS: { key: TabKey; label: string; sub: string; icon: typeof LayoutGrid }[] = [
  { key: 'overview', label: 'Overview', sub: 'Сектор', icon: LayoutGrid },
  { key: 'krystal', label: 'Krystal', sub: 'Сборка', icon: CircuitBoard },
  { key: 'personnel', label: 'Personnel', sub: 'Кадры', icon: Users },
  { key: 'projects', label: 'Projects', sub: 'Объекты', icon: FolderKanban },
  { key: 'comms', label: 'Comms', sub: 'Связь', icon: Radio },
  { key: 'chat', label: 'Command', sub: 'Директива', icon: MessageSquare },
  { key: 'deliverables', label: 'Results', sub: 'Результаты', icon: Package },
  { key: 'warehouse', label: 'Warehouse', sub: 'Склад', icon: Warehouse },
  { key: 'flow', label: 'Flow', sub: 'Поток', icon: Workflow },
  { key: 'devdept', label: 'Dev Dept', sub: 'Программисты', icon: Code2 },
];

export function TabNav({ activeTab, onTabChange }: TabNavProps) {
  return (
    <nav className="sticky top-0 z-50 bg-zone-50/95 backdrop-blur-sm border-b border-zone-300">
      <div className="max-w-7xl mx-auto px-4 md:px-6">
        <div className="flex items-stretch gap-0 overflow-x-auto">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const isActive = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                onClick={() => onTabChange(tab.key)}
                className={`group relative flex items-center gap-2.5 px-5 py-3.5 border-b-2 transition-all duration-200 flex-shrink-0
                  ${isActive ? 'tab-active' : 'tab-inactive'}`}
              >
                <Icon
                  className={`w-4 h-4 transition-colors ${isActive ? 'text-blood-500' : 'text-zone-600 group-hover:text-zone-700'}`}
                  strokeWidth={2}
                />
                <div className="text-left">
                  <div className={`font-display text-sm font-600 uppercase tracking-wide leading-none ${isActive ? 'text-zone-900' : ''}`}>
                    {tab.label}
                  </div>
                  <div className="font-mono text-[8px] tracking-widest uppercase text-zone-600 mt-0.5">
                    {tab.sub}
                  </div>
                </div>
                {isActive && (
                  <span className="absolute -bottom-px left-0 right-0 h-px bg-blood-500" />
                )}
              </button>
            );
          })}
        </div>
      </div>
    </nav>
  );
}
