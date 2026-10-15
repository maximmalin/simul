import { useEffect, useRef, useState, useCallback } from 'react';
import {
  Warehouse, Camera, ScanLine, Search, Plus, X, Loader2,
  Cpu, Zap, Box, Layers, Thermometer, Package, Tag,
  FileText, ChevronDown, ChevronUp, Trash2, Edit3, Save,
  AlertCircle, CheckCircle2, Hash, Factory, Ruler,
} from 'lucide-react';
import { supabase } from '@/lib/supabase';
import type { WarehouseComponent } from '@/lib/supabase';

const CATEGORIES = [
  'uncategorized', 'resistor', 'capacitor', 'inductor', 'diode',
  'transistor', 'microcontroller', 'connector', 'sensor', 'relay',
  'crystal', 'switch', 'led', 'ic', 'power', 'mechanical', 'cable',
];

function parseOcrText(text: string): Partial<WarehouseComponent> {
  const result: Partial<WarehouseComponent> = {
    raw_ocr_text: text,
    tags: [],
  };

  const lines = text.split('\n').map((l) => l.trim()).filter(Boolean);

  const allText = lines.join(' ');

  const partMatch = allText.match(/\b([A-Z]{2,}[\d\-A-Z]{3,})\b/);
  if (partMatch) result.part_number = partMatch[1];

  const valueMatch = allText.match(/(\d+(?:\.\d+)?)\s*(Ω|kΩ|MΩ|µF|uF|nF|pF|F|H|V|A|W|Hz|MHz|kHz)\b/i);
  if (valueMatch) {
    const num = valueMatch[1];
    const unit = valueMatch[2];
    result.value = `${num}${unit}`;
    if (unit.match(/Ω|kΩ|MΩ/i)) result.category = 'resistor';
    else if (unit.match(/F/i)) result.category = 'capacitor';
    else if (unit.match(/H/i)) result.category = 'inductor';
    else if (unit.match(/V/i)) result.voltage_rating = `${num}${unit}`;
    else if (unit.match(/W/i)) result.power_rating = `${num}${unit}`;
  }

  const tolMatch = allText.match(/±\s*(\d+(?:\.\d+)?)\s*%/i);
  if (tolMatch) result.tolerance = `±${tolMatch[1]}%`;

  const tempMatch = allText.match(/(-?\d+)\s*~?\s*[-+]\s*(\d+)\s*°C/i);
  if (tempMatch) result.temperature_range = `${tempMatch[1]} to +${tempMatch[2]}°C`;

  const pkgMatch = allText.match(/\b(DIP|SMD|SOT-?\d+|TO-?\d+|LQFP|TQFP|QFN|DFN|BGA|SOIC|TSSOP|MSOP|0\d{3}|1206|0805|0603|0402)\b/i);
  if (pkgMatch) result.package_type = pkgMatch[1].toUpperCase();

  const mfrMatch = allText.match(/\b(STMicro|Panasonic|Yageo|Samsung|TI|NXP|ON Semi|Microchip|Infineon|Vishay|Murata|TDK|Kyocera|AVX|HRO)\b/i);
  if (mfrMatch) result.manufacturer = mfrMatch[1];

  const knownMcus = allText.match(/\b(STM32\w*|ESP32\w*|ATmega\w*|ATtiny\w*|PIC\w*|nRF\w*|RP2040)\b/i);
  if (knownMcus) {
    result.category = 'microcontroller';
    result.part_number = knownMcus[1];
    result.name = knownMcus[1];
  }

  if (!result.name) {
    if (result.part_number) result.name = result.part_number;
    else if (lines.length > 0) result.name = lines[0].slice(0, 60);
    else result.name = 'Unknown Component';
  }

  const tags: string[] = [];
  if (result.category) tags.push(result.category);
  if (result.package_type) tags.push(result.package_type.toLowerCase());
  if (result.manufacturer) tags.push(result.manufacturer.toLowerCase());
  result.tags = tags;

  return result;
}

const CATEGORY_ICONS: Record<string, typeof Cpu> = {
  resistor: Zap,
  capacitor: Box,
  inductor: Layers,
  diode: Zap,
  transistor: Cpu,
  microcontroller: Cpu,
  connector: Package,
  sensor: ScanLine,
  relay: Box,
  crystal: Box,
  switch: Package,
  led: Zap,
  ic: Cpu,
  power: Zap,
  mechanical: Factory,
  cable: Package,
  uncategorized: Box,
};

export function WarehouseTab() {
  const [components, setComponents] = useState<WarehouseComponent[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterCategory, setFilterCategory] = useState('all');
  const [showCamera, setShowCamera] = useState(false);
  const [ocrProcessing, setOcrProcessing] = useState(false);
  const [ocrProgress, setOcrProgress] = useState(0);
  const [ocrResult, setOcrResult] = useState<Partial<WarehouseComponent> | null>(null);
  const [ocrImage, setOcrImage] = useState<string | null>(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);

  const [formData, setFormData] = useState<Partial<WarehouseComponent>>({
    name: '',
    part_number: '',
    manufacturer: '',
    category: 'uncategorized',
    package_type: '',
    value: '',
    tolerance: '',
    voltage_rating: '',
    power_rating: '',
    temperature_range: '',
    description: '',
    quantity: 0,
    location: '',
    tags: [],
  });

  const fetchComponents = useCallback(async () => {
    const { data } = await supabase
      .from('warehouse_components')
      .select('*')
      .order('created_at', { ascending: false });
    setComponents((data ?? []) as WarehouseComponent[]);
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchComponents();
  }, [fetchComponents]);

  const handleImageCapture = async (file: File) => {
    setShowCamera(false);
    setOcrProcessing(true);
    setOcrProgress(0);
    setOcrResult(null);

    const reader = new FileReader();
    reader.onload = async (e) => {
      const imageData = e.target?.result as string;
      setOcrImage(imageData);

      try {
        const Tesseract = (await import('tesseract.js')).default;
        const worker = await Tesseract.createWorker('eng', 1, {
          logger: (m: { status: string; progress: number }) => {
            if (m.status === 'recognizing text') {
              setOcrProgress(Math.round(m.progress * 100));
            }
          },
        });

        const { data: ocrData } = await worker.recognize(imageData);
        await worker.terminate();

        const parsed = parseOcrText(ocrData.text);
        setOcrResult(parsed);
        setFormData({
          ...parsed,
          name: parsed.name ?? '',
          part_number: parsed.part_number ?? '',
          manufacturer: parsed.manufacturer ?? '',
          category: parsed.category ?? 'uncategorized',
          package_type: parsed.package_type ?? '',
          value: parsed.value ?? '',
          tolerance: parsed.tolerance ?? '',
          voltage_rating: parsed.voltage_rating ?? '',
          power_rating: parsed.power_rating ?? '',
          temperature_range: parsed.temperature_range ?? '',
          description: '',
          quantity: 1,
          location: '',
          tags: parsed.tags ?? [],
        });
        setShowAddForm(true);
      } catch {
        setSaveStatus('error');
      } finally {
        setOcrProcessing(false);
      }
    };
    reader.readAsDataURL(file);
  };

  const handleSave = async () => {
    if (!formData.name?.trim()) return;
    setSaveStatus('saving');

    try {
      if (editingId) {
        const { error } = await supabase
          .from('warehouse_components')
          .update({
            name: formData.name,
            part_number: formData.part_number ?? null,
            manufacturer: formData.manufacturer ?? null,
            category: formData.category ?? 'uncategorized',
            package_type: formData.package_type ?? null,
            value: formData.value ?? null,
            tolerance: formData.tolerance ?? null,
            voltage_rating: formData.voltage_rating ?? null,
            power_rating: formData.power_rating ?? null,
            temperature_range: formData.temperature_range ?? null,
            description: formData.description ?? null,
            quantity: formData.quantity ?? 0,
            location: formData.location ?? null,
            tags: formData.tags ?? [],
            updated_at: new Date().toISOString(),
          })
          .eq('id', editingId);
        if (error) throw error;
      } else {
        const { error } = await supabase
          .from('warehouse_components')
          .insert({
            name: formData.name,
            part_number: formData.part_number ?? null,
            manufacturer: formData.manufacturer ?? null,
            category: formData.category ?? 'uncategorized',
            package_type: formData.package_type ?? null,
            value: formData.value ?? null,
            tolerance: formData.tolerance ?? null,
            voltage_rating: formData.voltage_rating ?? null,
            power_rating: formData.power_rating ?? null,
            temperature_range: formData.temperature_range ?? null,
            description: formData.description ?? null,
            quantity: formData.quantity ?? 0,
            location: formData.location ?? null,
            raw_ocr_text: formData.raw_ocr_text ?? null,
            tags: formData.tags ?? [],
          });
        if (error) throw error;
      }

      setSaveStatus('saved');
      setShowAddForm(false);
      setEditingId(null);
      setOcrResult(null);
      setOcrImage(null);
      setFormData({
        name: '', part_number: '', manufacturer: '', category: 'uncategorized',
        package_type: '', value: '', tolerance: '', voltage_rating: '',
        power_rating: '', temperature_range: '', description: '',
        quantity: 0, location: '', tags: [],
      });
      await fetchComponents();
      setTimeout(() => setSaveStatus('idle'), 2000);
    } catch {
      setSaveStatus('error');
      setTimeout(() => setSaveStatus('idle'), 3000);
    }
  };

  const handleEdit = (comp: WarehouseComponent) => {
    setEditingId(comp.id);
    setFormData({
      name: comp.name,
      part_number: comp.part_number,
      manufacturer: comp.manufacturer,
      category: comp.category,
      package_type: comp.package_type,
      value: comp.value,
      tolerance: comp.tolerance,
      voltage_rating: comp.voltage_rating,
      power_rating: comp.power_rating,
      temperature_range: comp.temperature_range,
      description: comp.description,
      quantity: comp.quantity,
      location: comp.location,
      tags: comp.tags,
    });
    setShowAddForm(true);
    setExpandedId(null);
  };

  const handleDelete = async (id: string) => {
    await supabase.from('warehouse_components').delete().eq('id', id);
    setComponents((prev) => prev.filter((c) => c.id !== id));
  };

  const handleCancel = () => {
    setShowAddForm(false);
    setEditingId(null);
    setOcrResult(null);
    setOcrImage(null);
    setFormData({
      name: '', part_number: '', manufacturer: '', category: 'uncategorized',
      package_type: '', value: '', tolerance: '', voltage_rating: '',
      power_rating: '', temperature_range: '', description: '',
      quantity: 0, location: '', tags: [],
    });
  };

  const filtered = components.filter((c) => {
    const q = searchQuery.toLowerCase();
    const matchesSearch = !searchQuery ||
      c.name.toLowerCase().includes(q) ||
      (c.part_number?.toLowerCase().includes(q) ?? false) ||
      (c.manufacturer?.toLowerCase().includes(q) ?? false) ||
      c.tags.some((t) => t.toLowerCase().includes(q));
    const matchesCategory = filterCategory === 'all' || c.category === filterCategory;
    return matchesSearch && matchesCategory;
  });

  const totalStock = components.reduce((sum, c) => sum + c.quantity, 0);
  const categoryCounts = CATEGORIES.reduce((acc, cat) => {
    acc[cat] = components.filter((c) => c.category === cat).length;
    return acc;
  }, {} as Record<string, number>);

  const updateField = (field: keyof WarehouseComponent, value: string | number | string[]) => {
    setFormData((prev) => ({ ...prev, [field]: value }));
  };

  return (
    <div className="px-4 md:px-6 py-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-5">
        <div className="w-8 h-8 bg-rust-600 flex items-center justify-center clip-corner-tl">
          <Warehouse className="w-4 h-4 text-zone-900" />
        </div>
        <div>
          <h2 className="font-display font-600 text-xl uppercase tracking-tight text-zone-900">
            Component Warehouse
          </h2>
          <p className="font-mono text-[10px] tracking-widest text-zone-600 uppercase">
            Склад · Camera OCR Inventory · Universal Format
          </p>
        </div>
        <div className="ml-auto flex items-center gap-3">
          <div className="text-right">
            <div className="font-mono text-[10px] text-zone-600 uppercase tracking-wider">{components.length} Types</div>
            <div className="font-mono text-[10px] text-toxic-400 tracking-wider">{totalStock.toLocaleString()} Units</div>
          </div>
        </div>
      </div>

      {/* Action bar */}
      <div className="flex flex-col md:flex-row gap-2 mb-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zone-600" />
          <input
            type="text"
            placeholder="Search by name, part number, manufacturer, tags..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full text-sm bg-zone-200 border border-zone-400/20 rounded-sm pl-9 pr-3 py-2 text-zone-900 placeholder-zone-600 focus:outline-none focus:border-blood-500"
          />
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowCamera(true)}
            className="flex items-center gap-1.5 bg-toxic-600/20 border border-toxic-500/30 hover:bg-toxic-600/30 transition-all px-3 py-2 rounded-sm clip-corner-tl"
          >
            <Camera className="w-4 h-4 text-toxic-400" />
            <span className="font-mono text-[10px] uppercase tracking-widest text-toxic-400">Scan</span>
          </button>
          <button
            onClick={() => { setEditingId(null); handleCancel(); setShowAddForm(true); }}
            className="flex items-center gap-1.5 bg-blood-600/20 border border-blood-500/30 hover:bg-blood-600/30 transition-all px-3 py-2 rounded-sm clip-corner-tl"
          >
            <Plus className="w-4 h-4 text-blood-500" />
            <span className="font-mono text-[10px] uppercase tracking-widest text-blood-500">Add</span>
          </button>
        </div>
      </div>

      {/* Category filter */}
      <div className="flex flex-wrap gap-1.5 mb-4">
        <button
          onClick={() => setFilterCategory('all')}
          className={`font-mono text-[9px] uppercase tracking-widest px-2.5 py-1 rounded-sm border transition-all
            ${filterCategory === 'all'
              ? 'bg-blood-600/20 border-blood-500/40 text-blood-500 font-bold'
              : 'bg-zone-200 border-zone-400/20 text-zone-600 hover:text-zone-800'}`}
        >
          All ({components.length})
        </button>
        {CATEGORIES.filter((c) => categoryCounts[c] > 0).map((cat) => (
          <button
            key={cat}
            onClick={() => setFilterCategory(cat)}
            className={`font-mono text-[9px] uppercase tracking-widest px-2.5 py-1 rounded-sm border transition-all
              ${filterCategory === cat
                ? 'bg-blood-600/20 border-blood-500/40 text-blood-500 font-bold'
                : 'bg-zone-200 border-zone-400/20 text-zone-600 hover:text-zone-800'}`}
          >
            {cat} ({categoryCounts[cat]})
          </button>
        ))}
      </div>

      {/* Camera capture modal */}
      {showCamera && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={() => setShowCamera(false)}>
          <div className="bg-zone-100 border border-zone-400/30 clip-corner max-w-md w-full p-6" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <Camera className="w-5 h-5 text-toxic-400" />
                <h3 className="font-display font-600 text-sm uppercase tracking-wide text-zone-900">Scan Component</h3>
              </div>
              <button onClick={() => setShowCamera(false)} className="text-zone-600 hover:text-zone-800">
                <X className="w-5 h-5" />
              </button>
            </div>
            <p className="text-xs text-zone-600 mb-4">
              Photograph a component with your smartphone camera. Text will be extracted via OCR and parsed into structured fields automatically.
            </p>
            <div className="space-y-2">
              <button
                onClick={() => cameraInputRef.current?.click()}
                className="w-full flex items-center justify-center gap-2 bg-toxic-600 border border-toxic-500/30 hover:bg-toxic-500 transition-all px-4 py-3 rounded-sm"
              >
                <Camera className="w-5 h-5 text-zone-900" />
                <span className="font-mono text-xs uppercase tracking-widest text-zone-900">Take Photo</span>
              </button>
              <button
                onClick={() => fileInputRef.current?.click()}
                className="w-full flex items-center justify-center gap-2 bg-zone-300 border border-zone-400/30 hover:bg-zone-400 transition-all px-4 py-3 rounded-sm"
              >
                <FileText className="w-5 h-5 text-zone-800" />
                <span className="font-mono text-xs uppercase tracking-widest text-zone-800">Upload Image</span>
              </button>
            </div>
            <input
              ref={cameraInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleImageCapture(file);
              }}
            />
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleImageCapture(file);
              }}
            />
          </div>
        </div>
      )}

      {/* OCR processing overlay */}
      {ocrProcessing && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4">
          <div className="bg-zone-100 border border-zone-400/30 clip-corner max-w-md w-full p-6 text-center">
            {ocrImage && (
              <div className="mb-4 relative">
                <img src={ocrImage} alt="Captured" className="w-full max-h-48 object-contain rounded-sm border border-zone-400/20" />
                <div className="absolute inset-0 bg-toxic-500/10 animate-pulse" />
              </div>
            )}
            <ScanLine className="w-8 h-8 text-toxic-400 mx-auto mb-3 animate-pulse" />
            <p className="font-mono text-xs uppercase tracking-widest text-zone-800 mb-2">
              OCR Processing...
            </p>
            <div className="w-full h-2 progress-track rounded-sm overflow-hidden mb-2">
              <div className="h-full bg-toxic-500 transition-all duration-300" style={{ width: `${ocrProgress}%` }} />
            </div>
            <p className="font-mono text-[10px] text-zone-600">{ocrProgress}%</p>
          </div>
        </div>
      )}

      {/* Add/Edit form */}
      {showAddForm && (
        <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-zone-100 border border-zone-400/30 clip-corner max-w-2xl w-full p-6 my-8" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                {ocrImage ? <ScanLine className="w-5 h-5 text-toxic-400" /> : <Plus className="w-5 h-5 text-blood-500" />}
                <h3 className="font-display font-600 text-sm uppercase tracking-wide text-zone-900">
                  {editingId ? 'Edit Component' : ocrResult ? 'OCR Parsed — Review & Save' : 'New Component'}
                </h3>
              </div>
              <button onClick={handleCancel} className="text-zone-600 hover:text-zone-800">
                <X className="w-5 h-5" />
              </button>
            </div>

            {ocrImage && (
              <div className="mb-4">
                <img src={ocrImage} alt="Captured" className="w-full max-h-32 object-contain rounded-sm border border-zone-400/20" />
                {ocrResult?.raw_ocr_text && (
                  <div className="mt-2 bg-zone-200/30 border border-zone-400/15 rounded-sm p-2 max-h-20 overflow-y-auto">
                    <div className="font-mono text-[8px] text-zone-600 uppercase tracking-widest mb-1">Raw OCR Text</div>
                    <pre className="font-mono text-[9px] text-zone-700 whitespace-pre-wrap">{ocrResult.raw_ocr_text}</pre>
                  </div>
                )}
              </div>
            )}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <FormField label="Name" icon={Tag} value={formData.name ?? ''} onChange={(v) => updateField('name', v)} />
              <FormField label="Part Number" icon={Hash} value={formData.part_number ?? ''} onChange={(v) => updateField('part_number', v)} />
              <FormField label="Manufacturer" icon={Factory} value={formData.manufacturer ?? ''} onChange={(v) => updateField('manufacturer', v)} />
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Category</label>
                <select
                  value={formData.category ?? 'uncategorized'}
                  onChange={(e) => updateField('category', e.target.value)}
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500"
                >
                  {CATEGORIES.map((cat) => (
                    <option key={cat} value={cat}>{cat}</option>
                  ))}
                </select>
              </div>
              <FormField label="Package Type" icon={Box} value={formData.package_type ?? ''} onChange={(v) => updateField('package_type', v)} />
              <FormField label="Value" icon={Zap} value={formData.value ?? ''} onChange={(v) => updateField('value', v)} />
              <FormField label="Tolerance" icon={Ruler} value={formData.tolerance ?? ''} onChange={(v) => updateField('tolerance', v)} />
              <FormField label="Voltage Rating" icon={Zap} value={formData.voltage_rating ?? ''} onChange={(v) => updateField('voltage_rating', v)} />
              <FormField label="Power Rating" icon={Zap} value={formData.power_rating ?? ''} onChange={(v) => updateField('power_rating', v)} />
              <FormField label="Temp Range" icon={Thermometer} value={formData.temperature_range ?? ''} onChange={(v) => updateField('temperature_range', v)} />
              <FormField label="Location" icon={Package} value={formData.location ?? ''} onChange={(v) => updateField('location', v)} />
              <div>
                <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Quantity</label>
                <input
                  type="number"
                  value={formData.quantity ?? 0}
                  onChange={(e) => updateField('quantity', parseInt(e.target.value) || 0)}
                  className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500"
                />
              </div>
            </div>
            <div className="mt-3">
              <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Description</label>
              <textarea
                value={formData.description ?? ''}
                onChange={(e) => updateField('description', e.target.value)}
                rows={2}
                className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500 resize-none"
              />
            </div>
            <div className="mt-3">
              <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">Tags (comma-separated)</label>
              <input
                type="text"
                value={(formData.tags ?? []).join(', ')}
                onChange={(e) => updateField('tags', e.target.value.split(',').map((t) => t.trim()).filter(Boolean))}
                className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm px-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500"
              />
            </div>

            <div className="flex items-center gap-2 mt-5">
              <button
                onClick={handleSave}
                disabled={!formData.name?.trim() || saveStatus === 'saving'}
                className="flex items-center gap-1.5 bg-blood-600 text-zone-900 px-4 py-2 rounded-sm hover:bg-blood-500 transition-colors disabled:opacity-30 clip-corner-tl"
              >
                {saveStatus === 'saving' ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                <span className="font-mono text-[10px] uppercase tracking-widest">
                  {editingId ? 'Update' : 'Save to Warehouse'}
                </span>
              </button>
              {saveStatus === 'saved' && (
                <span className="flex items-center gap-1 text-toxic-400">
                  <CheckCircle2 className="w-4 h-4" />
                  <span className="font-mono text-[10px] uppercase tracking-widest">Saved</span>
                </span>
              )}
              {saveStatus === 'error' && (
                <span className="flex items-center gap-1 text-blood-500">
                  <AlertCircle className="w-4 h-4" />
                  <span className="font-mono text-[10px] uppercase tracking-widest">Error</span>
                </span>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Component list */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="w-8 h-8 border-4 border-blood-600 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-zone-600">
          <AlertCircle className="w-8 h-8 mb-2" />
          <p className="font-mono text-xs uppercase tracking-widest">No components found</p>
          <p className="font-mono text-[10px] text-zone-600/50 mt-1">Scan with camera or add manually</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map((comp) => {
            const CatIcon = CATEGORY_ICONS[comp.category] ?? Box;
            const isExpanded = expandedId === comp.id;
            const stockLow = comp.quantity < 50;
            const stockOut = comp.quantity === 0;

            return (
              <div
                key={comp.id}
                className={`bg-zone-panel border overflow-hidden clip-corner transition-all
                  ${isExpanded ? 'border-blood-500/30' : 'border-zone-400/20 hover:border-zone-500/40'}`}
              >
                {/* Top bar */}
                <div className="flex items-center gap-2 px-3 py-2 border-b border-zone-400/15 bg-zone-200/20">
                  <div className="w-7 h-7 bg-zone-300/30 flex items-center justify-center rounded-sm">
                    <CatIcon className="w-3.5 h-3.5 text-zone-700" />
                  </div>
                  <span className="font-mono text-[8px] tracking-widest text-zone-600 uppercase">
                    {comp.category}
                  </span>
                  {comp.package_type && (
                    <span className="font-mono text-[8px] text-zone-600/50">· {comp.package_type}</span>
                  )}
                  <div className="ml-auto flex items-center gap-1.5">
                    <span className={`font-mono text-[10px] font-bold tabular-nums
                      ${stockOut ? 'text-blood-500' : stockLow ? 'text-amber-400' : 'text-toxic-400'}`}>
                      {comp.quantity}
                    </span>
                    <span className="font-mono text-[8px] text-zone-600 uppercase">qty</span>
                  </div>
                </div>

                {/* Body */}
                <div className="p-3">
                  <h3 className="font-display font-600 text-sm uppercase tracking-tight text-zone-900 mb-0.5">
                    {comp.name}
                  </h3>
                  {comp.part_number && (
                    <p className="font-mono text-[10px] text-blood-500/80 tracking-wider mb-0.5">
                      {comp.part_number}
                    </p>
                  )}
                  {comp.manufacturer && (
                    <p className="font-mono text-[9px] text-zone-600 tracking-wider mb-1">
                      {comp.manufacturer}
                    </p>
                  )}

                  {/* Key specs */}
                  <div className="flex flex-wrap gap-1 mb-2">
                    {comp.value && <SpecChip label={comp.value} />}
                    {comp.tolerance && <SpecChip label={comp.tolerance} />}
                    {comp.voltage_rating && <SpecChip label={comp.voltage_rating} />}
                    {comp.power_rating && <SpecChip label={comp.power_rating} />}
                  </div>

                  {/* Tags */}
                  {comp.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2">
                      {comp.tags.slice(0, 4).map((tag) => (
                        <span key={tag} className="font-mono text-[8px] uppercase tracking-wider px-1.5 py-0.5 bg-zone-300/30 border border-zone-400/15 text-zone-600 rounded-sm">
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}

                  {/* Location */}
                  {comp.location && (
                    <div className="flex items-center gap-1 mb-2">
                      <Package className="w-3 h-3 text-zone-600" />
                      <span className="font-mono text-[9px] text-zone-600 tracking-wider">
                        Bin: {comp.location}
                      </span>
                    </div>
                  )}

                  {/* Actions */}
                  <div className="flex items-center gap-2 pt-2 border-t border-zone-400/15">
                    <button
                      onClick={() => setExpandedId(isExpanded ? null : comp.id)}
                      className="flex items-center gap-1 font-mono text-[9px] uppercase tracking-widest text-zone-600 hover:text-zone-800 transition-colors"
                    >
                      {isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                      Details
                    </button>
                    <button
                      onClick={() => handleEdit(comp)}
                      className="flex items-center gap-1 font-mono text-[9px] uppercase tracking-widest text-zone-600 hover:text-amber-400 transition-colors ml-auto"
                    >
                      <Edit3 className="w-3 h-3" />
                      Edit
                    </button>
                    <button
                      onClick={() => handleDelete(comp.id)}
                      className="flex items-center gap-1 font-mono text-[9px] uppercase tracking-widest text-zone-600 hover:text-blood-500 transition-colors"
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>

                  {/* Expanded details */}
                  {isExpanded && (
                    <div className="mt-2 pt-2 border-t border-zone-400/15 animate-slide-up space-y-1.5">
                      {comp.description && (
                        <p className="text-[11px] text-zone-700 leading-snug">{comp.description}</p>
                      )}
                      {comp.temperature_range && (
                        <div className="flex items-center gap-1.5">
                          <Thermometer className="w-3 h-3 text-zone-600" />
                          <span className="font-mono text-[10px] text-zone-600">{comp.temperature_range}</span>
                        </div>
                      )}
                      {comp.raw_ocr_text && (
                        <div className="bg-zone-200/30 border border-zone-400/15 rounded-sm p-2 mt-1">
                          <div className="font-mono text-[8px] text-zone-600 uppercase tracking-widest mb-1">OCR Source</div>
                          <pre className="font-mono text-[8px] text-zone-600 whitespace-pre-wrap break-words max-h-16 overflow-y-auto">{comp.raw_ocr_text}</pre>
                        </div>
                      )}
                      <div className="font-mono text-[8px] text-zone-600/50">
                        Added: {new Date(comp.created_at).toLocaleDateString('en-GB')}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function FormField({ label, icon: Icon, value, onChange }: {
  label: string;
  icon: typeof Tag;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="font-mono text-[9px] tracking-widest text-zone-600 uppercase block mb-1">{label}</label>
      <div className="relative">
        <Icon className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-zone-600" />
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full text-xs bg-zone-200 border border-zone-400/20 rounded-sm pl-7 pr-2 py-1.5 text-zone-900 focus:outline-none focus:border-blood-500"
        />
      </div>
    </div>
  );
}

function SpecChip({ label }: { label: string }) {
  return (
    <span className="font-mono text-[9px] px-1.5 py-0.5 bg-toxic-700/10 border border-toxic-600/20 text-toxic-400 rounded-sm">
      {label}
    </span>
  );
}
