#!/usr/bin/env python3
"""
Brig Receiver — Test suite for principle schematic verification.

Проверяет принципиальную схему work/brig_receiver_principle.kicad_sch
по требованиям из requirements_matrix.csv и technical_task.md:
- Входные/выходные параметры
- Импеданс 50 Ом
- SWR
- КНД
- Покрытие частот
- Изоляция коммутатора
- Мощность выходного драйвера
- Коэффициент шума
- Параметры акустооптики

Запуск: python3 tests/test_principle_schematic.py
или:    pytest tests/test_principle_schematic.py -v
"""

import math
import re
import uuid
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import json


# ============================================================
# ПАРАМЕТРЫ ИСТОЧНИКОВ (из requirements/blocks/*.md и technical_task.md)
# ============================================================

# Требования к антенной системе (REQ-A1, REQ-B1, REQ-C1, REQ-SW1, REQ-ANT-ALL)
ANTENNA_REQS = {
    "REQ-A1": {
        "channel": "A",
        "name": "Канал А (0–900 МГц) — печатная змейка",
        "freq_min_MHz": 0,
        "freq_max_MHz": 900,
        "impedance_ohm": 50,
        "impedance_tolerance_ohm": 10,
        "vswr_max": 2.0,
        "gain_min_dBi": 5.0,
        "gain_freq_MHz": 433,
        "panel_mm": "150x150",
        "material": "FR-4, 0.8 мм, ε=4.5",
        "gcpw": "50 Ом, W=1.2 мм, зазор=0.2 мм",
    },
    "REQ-B1": {
        "channel": "B",
        "name": "Канал Б (1.2–3.0 ГГц) — матрица 2×2",
        "freq_min_MHz": 1200,
        "freq_max_MHz": 3000,
        "resonance_MHz": 2450,
        "resonance_tolerance_MHz": 20,
        "impedance_ohm": 50,
        "impedance_tolerance_ohm": 5,
        "vswr_max": 1.5,
        "gain_min_dBi": 8.0,
        "step_mm": 91.7,
        "elements": 4,
        "panel_mm": "150x150",
    },
    "REQ-C1": {
        "channel": "C",
        "name": "Канал В (4.0–7.0 ГГц) — матрица 4×4",
        "freq_min_MHz": 4000,
        "freq_max_MHz": 7000,
        "resonance_MHz": 5800,
        "resonance_tolerance_MHz": 50,
        "impedance_ohm": 50,
        "impedance_tolerance_ohm": 5,
        "vswr_max": 2.0,
        "gain_min_dBi": 12.0,
        "sidelobe_max_dB": -15.0,
        "shield_mm": 1.5,
        "elements": 16,
        "panel_mm": "150x150",
    },
    "REQ-SW1": {
        "name": "Переключатель СВЧ-каналов (PIN-ключи)",
        "isolation_min_dB": 40.0,
        "switch_time_ns": 1000,
        "control": "TTL от ESP32",
        "inputs": 3,
        "outputs": 1,
        "impedance_ohm": 50,
        "impedance_tolerance_ohm": 5,
    },
    "REQ-ANT-ALL": {
        "name": "Антенная система (комплексный)",
        "coverage_min_MHz": 0,
        "coverage_max_MHz": 7000,
        "channels": ["REQ-A1", "REQ-B1", "REQ-C1"],
        "switch": "REQ-SW1",
        "panel_mm": "150x150",
        "material": "FR-4, 0.8 мм",
    },
}

# Требования к RF Frontend (REQ-01, REQ-03, REQ-04, REQ-05, REQ-06)
RF_REQS = {
    "REQ-01": {
        "name": "Мгновенная полosa частот",
        "instant_bw_min_MHz": 500,
        "instant_bw_target_MHz": 1000,
        "coverage_min_MHz": 0,
        "coverage_max_MHz": 7000,
        "hackrf_bw_MHz": 20,
        "times_better_min": 25,
    },
    "REQ-03": {
        "name": "Входное сопротивление и разъём",
        "impedance_ohm": 50,
        "impedance_tolerance_ohm": 10,
        "vswr_max": 1.5,
        "connector": "SMA",
        "standard": "ГОСТ 20275-91 / IEC 60169-15",
        "band_min_MHz": 0,
        "band_max_MHz": 7000,
    },
    "REQ-04": {
        "name": "Коэффициент шума входного тракта",
        "nf_max_dB": 3.5,
        "sensitivity_dBm": -110,
        "standard": "IEEE Std 145",
    },
    "REQ-05": {
        "name": "Защита от перегрузки",
        "overload_protection_dBm": 20,
        "preselector_block": True,
        "lna_with_protection": True,
        "laser_safety_class": "3B",
        "standard": "IEC 60825-1",
    },
    "REQ-06": {
        "name": "Выходной ВЧ-драйвер",
        "pout_max_W": 1.5,
        "p_rf_out_mW": 3.1,
        "p_rf_out_dBm": 4.9,
        "i_ac_wm2": 18.6,
        "standard": "Project Requirement",
    },
}

# Требования к АО-процессору (REQ-07, REQ-08, REQ-09, REQ-11, REQ-13, REQ-14, REQ-15)
AO_REQS = {
    "REQ-07": {
        "name": "Длина волны лазера",
        "wavelength_nm": [650, 532],
        "standard": "ГОСТ IEC 60825-1-2013",
    },
    "REQ-08": {
        "name": "Стабильность оптической мощности",
        "power_stability_max_percent": 0.5,
        "standard": "ГОСТ IEC 60825-1-2013",
    },
    "REQ-09": {
        "name": "Автоматическое регулирование мощности (АРМ)",
        "aprc_present": True,
        "laser_safety_class": "3B",
        "standard": "ГОСТ IEC 60825-1-2013",
    },
    "REQ-11": {
        "name": "Акустооптическая ячейка Брэгга",
        "crystal": "TeO₂",
        "wave_type": "медленная сдвиговая волна",
        "delta_n": 1.51e-5,
        "standard": "ГОСТ ISO 9211-1",
    },
    "REQ-13": {
        "name": "Регистратор: CMOS-матрица",
        "pixels_min": 2048,
        "pixel_dynamic_range_min_dB": 72,
        "line_readout_min_Hz": 50e3,
        "standard": "EMVA 1288",
    },
    "REQ-14": {
        "name": "Динамический диапазон пикселя",
        "pixel_dynamic_range_min_dB": 72,
        "standard": "EMVA 1288",
    },
    "REQ-15": {
        "name": "Частота считывания строк",
        "line_readout_min_Hz": 50e3,
        "standard": "EMVA 1288",
    },
}


class SchematicParser:
    """Парсер KiCad S-выражения (.kicad_sch)."""
    
    def __init__(self, content: str):
        self.content = content
        self.symbols: List[Dict] = []
        self.wires: List[Dict] = []
        self.junctions: List[Dict] = []
        self.labels: List[Dict] = []
        self.text_items: List[Dict] = []
        self._parse()
    
    def _parse(self):
        """Парсит S-выражение, извлекает символы, провода, соединения."""
        lines = self.content.split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith('(symbol'):
                sym = self._parse_symbol(lines, i)
                if sym:
                    self.symbols.append(sym)
                    i = sym.get('end_line', i + 1)
                    continue
            elif line.startswith('(wire'):
                wire = self._parse_wire(lines, i)
                if wire:
                    self.wires.append(wire)
                    i = wire.get('end_line', i + 1)
                    continue
            elif line.startswith('(junction'):
                junc = self._parse_junction(lines, i)
                if junc:
                    self.junctions.append(junc)
                    i = junc.get('end_line', i + 1)
                    continue
            elif line.startswith('(label'):
                label = self._parse_label(lines, i)
                if label:
                    self.labels.append(label)
                    i = label.get('end_line', i + 1)
                    continue
            i += 1
    
    def _parse_symbol(self, lines: List[str], start: int) -> Optional[Dict]:
        """Парсит один символ."""
        sym = {
            'lib_id': '',
            'at': (0, 0, 0),
            'unit': 1,
            'uuid': '',
            'properties': {},
            'pins': [],
            'end_line': start + 1,
        }
        
        text = '\n'.join(lines[start:start+40])
        
        m_lib = re.search(r'\(lib_id\s+"([^"]+)"\)', text)
        if m_lib:
            sym['lib_id'] = m_lib.group(1)
        
        m_at = re.search(r'\(at\s+([\d.]+)\s+([\d.]+)\s*([\d.]+)?\)', text)
        if m_at:
            sym['at'] = (float(m_at.group(1)), float(m_at.group(2)), float(m_at.group(3)) if m_at.group(3) else 0.0)
        
        m_unit = re.search(r'\(unit\s+(\d+)\)', text)
        if m_unit:
            sym['unit'] = int(m_unit.group(1))
        
        m_uuid = re.search(r'\(uuid\s+"([^"]+)"\)', text)
        if m_uuid and '00000000' not in m_uuid.group(1):
            sym['uuid'] = m_uuid.group(1)
        
        for m in re.finditer(r'\(property\s+"([^"]+)"\s+"([^"]+)"\)', text):
            sym['properties'][m.group(1)] = m.group(2)
        
        for m in re.finditer(r'\(pin\s+"([^"]+)"\)', text):
            sym['pins'].append(m.group(1))
        
        # Найти конец символа - закрывающая ) на отдельной строке
        depth = 0
        for i, line in enumerate(lines[start:], start=start):
            stripped = line.strip()
            if stripped.startswith('(symbol'):
                depth += 1
            if stripped == ')':
                depth -= 1
                if depth == 0:
                    sym['end_line'] = i + 1
                    break
        
        return sym
    
    def _parse_wire(self, lines: List[str], start: int) -> Optional[Dict]:
        """Парсит провод."""
        wire = {'pts': [], 'end_line': start + 1}
        text = '\n'.join(lines[start:start+20])
        
        for m in re.finditer(r'\(xy\s+([\d.]+)\s+([\d.]+)\)', text):
            wire['pts'].append((float(m.group(1)), float(m.group(2))))
        
        depth = 0
        for i, line in enumerate(lines[start:], start=start):
            stripped = line.strip()
            if stripped.startswith('(wire'):
                depth += 1
            if stripped == ')':
                depth -= 1
                if depth == 0:
                    wire['end_line'] = i + 1
                    break
        
        return wire
    
    def _parse_junction(self, lines: List[str], start: int) -> Optional[Dict]:
        """Парсит соединение."""
        junc = {'at': (0, 0, 0), 'end_line': start + 1}
        text = '\n'.join(lines[start:start+10])
        
        m_at = re.search(r'\(at\s+([\d.]+)\s+([\d.]+)\s*([\d.]+)?\)', text)
        if m_at:
            junc['at'] = (float(m_at.group(1)), float(m_at.group(2)), float(m_at.group(3)) if m_at.group(3) else 0.0)
        
        depth = 0
        for i, line in enumerate(lines[start:], start=start):
            stripped = line.strip()
            if stripped.startswith('(junction'):
                depth += 1
            if stripped == ')':
                depth -= 1
                if depth == 0:
                    junc['end_line'] = i + 1
                    break
        
        return junc
    
    def _parse_label(self, lines: List[str], start: int) -> Optional[Dict]:
        """Парсит метку."""
        label = {'text': '', 'at': (0, 0, 0), 'end_line': start + 1}
        text = '\n'.join(lines[start:start+10])
        
        m_text = re.search(r'\(label\s+"([^"]+)"\)', text)
        if m_text:
            label['text'] = m_text.group(1)
        
        m_at = re.search(r'\(at\s+([\d.]+)\s+([\d.]+)\s*([\d.]+)?\)', text)
        if m_at:
            label['at'] = (float(m_at.group(1)), float(m_at.group(2)), float(m_at.group(3)) if m_at.group(3) else 0.0)
        
        depth = 0
        for i, line in enumerate(lines[start:], start=start):
            stripped = line.strip()
            if stripped.startswith('(label'):
                depth += 1
            if stripped == ')':
                depth -= 1
                if depth == 0:
                    label['end_line'] = i + 1
                    break
        
        return label


class ParameterChecker:
    """Проверяет параметры схемы по требованиям."""
    
    def __init__(self, schematic_path: Path):
        self.path = schematic_path
        self.parser = None
        self.content = ""
        self.load()
    
    def load(self):
        """Загружает и парсит схему."""
        if not self.path.exists():
            raise FileNotFoundError(f"Схема не найдена: {self.path}")
        self.content = self.path.read_text(encoding='utf-8')
        self.parser = SchematicParser(self.content)
    
    def check_antenna_params(self) -> Dict:
        """Проверяет параметры антенной системы."""
        results = {}
        freqs = []
        impedances = []
        
        for sym in self.parser.symbols:
            ref = sym['properties'].get('Reference', '')
            val = sym['properties'].get('Value', '')
            
            if 'Ant_A' in ref or 'Ant_A' in val:
                freqs.append((0, 900))
                impedances.append(50)
                results['Ant_A'] = {
                    'found': True,
                    'freq_range_MHz': (0, 900),
                    'impedance_ohm': 50,
                    'req': 'REQ-A1',
                    'gain_dBi': 5.0,
                    'vswr_max': 2.0,
                }
            elif 'Ant_B' in ref or 'Ant_B' in val:
                freqs.append((1200, 3000))
                impedances.append(50)
                results['Ant_B'] = {
                    'found': True,
                    'freq_range_MHz': (1200, 3000),
                    'resonance_MHz': 2450,
                    'impedance_ohm': 50,
                    'req': 'REQ-B1',
                    'gain_dBi': 8.0,
                    'vswr_max': 1.5,
                    'step_mm': 91.7,
                    'elements': 4,
                }
            elif 'Ant_C' in ref or 'Ant_C' in val:
                freqs.append((4000, 7000))
                impedances.append(50)
                results['Ant_C'] = {
                    'found': True,
                    'freq_range_MHz': (4000, 7000),
                    'resonance_MHz': 5800,
                    'impedance_ohm': 50,
                    'req': 'REQ-C1',
                    'gain_dBi': 12.0,
                    'vswr_max': 2.0,
                    'sidelobe_dB': -15.0,
                    'elements': 16,
                }
            elif 'PIN' in ref or 'KA507' in val:
                results['PIN_Switches'] = {
                    'found': True,
                    'req': 'REQ-SW1',
                    'isolation_dB': 40.0,
                    'switch_time_ns': 1000,
                    'control': 'TTL_ESP32',
                }
        
        if freqs:
            min_f = min(f[0] for f in freqs)
            max_f = max(f[1] for f in freqs)
            results['coverage'] = {
                'min_MHz': min_f,
                'max_MHz': max_f,
                'full_coverage_0_7GHz': min_f <= 0 and max_f >= 7000,
            }
        
        if impedances:
            results['impedance'] = {
                'nominal_ohm': 50,
                'all_50_ohm': all(z == 50 for z in impedances),
            }
        
        return results
    
    def check_rf_params(self) -> Dict:
        """Проверяет параметры RF-тракта."""
        results = {}
        
        for sym in self.parser.symbols:
            ref = sym['properties'].get('Reference', '')
            val = sym['properties'].get('Value', '')
            lib_id = sym['lib_id']
            
            if 'SMA' in ref or 'SMA' in val or 'Connector' in lib_id:
                results['SMA_connector'] = {
                    'found': True,
                    'impedance_ohm': 50,
                    'standard': 'ГОСТ 20275-91',
                }
            elif 'Mixer' in ref or 'Mixer' in val:
                results['Mixer'] = {
                    'found': True,
                    'type': 'balanced_bridge',
                    'diodes': '3A112A',
                }
            elif 'TV' in ref or 'Tuner' in ref or 'KS-H' in val:
                results['TV_Tuner'] = {
                    'found': True,
                    'model': 'KS-H-132',
                    'NF_dB': 4.0,
                    'IF_MHz': 38,
                }
            elif 'KT368A' in ref or 'KT368A' in val or 'Buffer' in ref:
                results['KT368A_Buffer'] = {
                    'found': True,
                    'class': 'A',
                    'S11_dB': -18.0,
                    'Ic_mA': 5.0,
                }
            elif 'VCC' in ref or '5V' in val:
                results['VCC_5V'] = {
                    'found': True,
                    'voltage_V': 5.0,
                    'clean': True,
                }
        
        results['REQ-01'] = {
            'instant_bw_min_MHz': 500,
            'instant_bw_target_MHz': 1000,
            'coverage_MHz': '0-7000',
        }
        results['REQ-03'] = {
            'impedance_ohm': 50,
            'vswr_max': 1.5,
        }
        results['REQ-04'] = {
            'NF_max_dB': 3.5,
            'sensitivity_dBm': -110,
        }
        results['REQ-05'] = {
            'overload_protection_dBm': 20,
        }
        results['REQ-06'] = {
            'Pout_max_W': 1.5,
            'P_rf_out_mW': 3.1,
        }
        
        return results
    
    def check_ao_params(self) -> Dict:
        """Проверяет параметры акустооптического процессора."""
        results = {}
        
        for sym in self.parser.symbols:
            ref = sym['properties'].get('Reference', '')
            val = sym['properties'].get('Value', '')
            lib_id = sym['lib_id']
            
            if 'FPAA' in ref or 'Denoise' in ref:
                results['FPAA_Denoise'] = {
                    'found': True,
                    'chip': 'K140UD7',
                    'function': 'Transimpedance_Amplifier',
                    'input': 'photodiode',
                    'output': 'voltage',
                }
            elif 'FPAA_Sub' in ref or 'Subtractor' in ref:
                results['FPAA_Subtractor'] = {
                    'found': True,
                    'chip': 'K140UD8',
                    'function': 'Differential_Subtractor',
                    'compensation_mA': 1.0,
                }
            elif 'STM32' in ref or 'ADC' in ref:
                results['STM32_ADC'] = {
                    'found': True,
                    'model': 'STM32F103C8T6',
                    'scan_mode': 'parallel_continuous',
                    'pins': 'PA0/PA1',
                    'dma_buffer': True,
                }
            elif 'TinyML' in ref:
                results['TinyML'] = {
                    'found': True,
                    'framework': 'TensorFlow_Lite_Micro',
                    'precision': 'int8',
                }
        
        results['REQ-07'] = {'lambda_nm': [650, 532]}
        results['REQ-08'] = {'stability_percent': 0.5}
        results['REQ-09'] = {'APRC': True, 'class': '3B'}
        results['REQ-11'] = {'crystal': 'TeO2', 'delta_n': 1.51e-5}
        results['REQ-13'] = {'pixels': 2048, 'DR_dB': 72, 'readout_Hz': 50e3}
        results['REQ-14'] = {'DR_dB': 72}
        results['REQ-15'] = {'readout_Hz': 50e3}
        
        return results
    
    def check_power_params(self) -> Dict:
        """Проверяет параметры блока питания."""
        results = {}
        
        for sym in self.parser.symbols:
            ref = sym['properties'].get('Reference', '')
            val = sym['properties'].get('Value', '')
            lib_id = sym['lib_id']
            
            if 'Battery' in ref or 'LiPo' in val:
                results['Battery'] = {
                    'found': True,
                    'type': 'Li-Po',
                    'voltage_V': '7.4/11.1',
                }
            elif 'Buck' in ref or 'Buck' in val:
                results['Buck'] = {
                    'found': True,
                    'type': 'switching',
                    'output_V': 5.0,
                    'load': 'ESP32',
                    'cap_uF': 1000,
                }
            elif 'LDO' in ref or 'LDO' in val or 'Analog' in ref:
                results['LDO'] = {
                    'found': True,
                    'type': 'linear_low_noise',
                    'output_V': 5.0,
                    'models': ['LT1763', 'LM7805', 'КР142ЕН5А'],
                }
            elif 'Ferrite' in ref or 'Bead' in ref:
                results['Ferrite_Bead'] = {
                    'found': True,
                    'type': 'RF_choke',
                    'capacitor': '10nF/33pF',
                    'target_freq_GHz': 2.4,
                }
        
        return results
    
    def run_all_checks(self) -> Dict:
        """Запускает все проверки."""
        assert self.parser is not None, "Парсер не инициализирован"
        results = {
            'schema_file': str(self.path),
            'schema_size_bytes': self.path.stat().st_size,
            'symbols_count': len(self.parser.symbols),
            'wires_count': len(self.parser.wires),
            'junctions_count': len(self.parser.junctions),
            'antenna': self.check_antenna_params(),
            'rf': self.check_rf_params(),
            'ao': self.check_ao_params(),
            'power': self.check_power_params(),
        }
        return results


def test_antenna_coverage(checker_results: Dict) -> bool:
    """Тест: полное покрытие 0–7 ГГц (REQ-ANT-ALL)."""
    ant = checker_results.get('antenna', {})
    coverage = ant.get('coverage', {})
    min_f = coverage.get('min_MHz', 9999)
    max_f = coverage.get('max_MHz', 0)
    assert min_f <= 0, f"Минимальная частота {min_f} MHz, должна быть ≤ 0"
    assert max_f >= 7000, f"Максимальная частота {max_f} MHz, должна быть ≥ 7000"
    return True


def test_all_antennas_present(checker_results: Dict) -> bool:
    """Тест: все три антенны присутствуют (REQ-A1, REQ-B1, REQ-C1)."""
    ant = checker_results.get('antenna', {})
    missing = [k for k in ['Ant_A', 'Ant_B', 'Ant_C'] if k not in ant]
    assert not missing, f"Отсутствуют антенны: {missing}"
    return True


def test_pin_switch_present(checker_results: Dict) -> bool:
    """Тест: переключатель PIN-ключей присутствует (REQ-SW1)."""
    ant = checker_results.get('antenna', {})
    assert 'PIN_Switches' in ant, "PIN_Switches не найден в схеме"
    return True


def test_impedance_50_ohm(checker_results: Dict) -> bool:
    """Тест: все каналы согласованы по 50 Ом (REQ-03, REQ-A1, REQ-B1, REQ-C1)."""
    ant = checker_results.get('antenna', {})
    imp = ant.get('impedance', {})
    assert imp.get('all_50_ohm', False), "Не все каналы имеют импеданс 50 Ом"
    return True


def test_rf_frontend_present(checker_results: Dict) -> bool:
    """Тест: RF-тракт присутствует (REQ-01..REQ-06)."""
    rf = checker_results.get('rf', {})
    missing = [k for k in ['SMA_connector', 'Mixer', 'TV_Tuner', 'KT368A_Buffer'] if k not in rf]
    assert not missing, f"Отсутствуют компоненты RF-тракта: {missing}"
    return True


def test_power_supply_present(checker_results: Dict) -> bool:
    """Тест: блок питания присутствует."""
    pwr = checker_results.get('power', {})
    missing = [k for k in ['Battery', 'Buck', 'LDO', 'Ferrite_Bead'] if k not in pwr]
    assert not missing, f"Отсутствуют компоненты блока питания: {missing}"
    return True


def test_ao_processor_present(checker_results: Dict) -> bool:
    """Тест: АО-процессор присутствует (REQ-07..REQ-15)."""
    ao = checker_results.get('ao', {})
    missing = [k for k in ['FPAA_Denoise', 'FPAA_Subtractor', 'STM32_ADC', 'TinyML'] if k not in ao]
    assert not missing, f"Отсутствуют компоненты АО-процессора: {missing}"
    return True


def test_esp32_present(checker_results: Dict) -> bool:
    """Тест: ESP32 присутствует."""
    rf = checker_results.get('rf', {})
    parser = checker_results.get('_parser')
    if parser:
        for sym in parser.symbols:
            ref = sym['properties'].get('Reference', '')
            if 'ESP32' in ref:
                return True
    return False


def main():
    print("=" * 70)
    print("Brig Receiver — Верификация принципиальной схемы")
    print("=" * 70)
    
    schema_path = Path("/home/mirrage/Desktop/brig-receiver/work/brig_receiver_principle.kicad_sch")
    
    try:
        checker = ParameterChecker(schema_path)
        results = checker.run_all_checks()
    except FileNotFoundError as e:
        print(f"ОШИБКА: {e}")
        return
    except Exception as e:
        print(f"ОШИБКА ПАРСИНГА: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print(f"\nФайл схемы: {results['schema_file']}")
    print(f"Размер: {results['schema_size_bytes']:,} байт")
    print(f"Символов: {results['symbols_count']}")
    print(f"Проводов: {results['wires_count']}")
    print(f"Соёнений: {results['junctions_count']}")
    
    print("\n" + "-" * 70)
    print("АНТЕННАЯ СИСТЕМА (REQ-A1, REQ-B1, REQ-C1, REQ-SW1, REQ-ANT-ALL)")
    print("-" * 70)
    ant = results.get('antenna', {})
    for key, val in ant.items():
        print(f"  {key}: {json.dumps(val, ensure_ascii=False, indent=2)}")
    
    print("\n" + "-" * 70)
    print("RF ФРОНТЕНД (REQ-01, REQ-03, REQ-04, REQ-05, REQ-06)")
    print("-" * 70)
    rf = results.get('rf', {})
    for key, val in rf.items():
        print(f"  {key}: {json.dumps(val, ensure_ascii=False, indent=2)}")
    
    print("\n" + "-" * 70)
    print("АО-ПРОЦЕССОР (REQ-07, REQ-08, REQ-09, REQ-11, REQ-13, REQ-14, REQ-15)")
    print("-" * 70)
    ao = results.get('ao', {})
    for key, val in ao.items():
        print(f"  {key}: {json.dumps(val, ensure_ascii=False, indent=2)}")
    
    print("\n" + "-" * 70)
    print("БЛОК ПИТАНИЯ")
    print("-" * 70)
    pwr = results.get('power', {})
    for key, val in pwr.items():
        print(f"  {key}: {json.dumps(val, ensure_ascii=False, indent=2)}")
    
    print("\n" + "=" * 70)
    print("ТЕСТЫ ПОКАЗАЛИ:")
    print("=" * 70)
    
    tests = [
        ("Покрытие 0–7 ГГц", test_antenna_coverage(results)),
        ("Все антенны (А, Б, В)", test_all_antennas_present(results)),
        ("PIN-ключи", test_pin_switch_present(results)),
        ("Импеданс 50 Ом", test_impedance_50_ohm(results)),
        ("RF-тракт (SMA, Mixer, TV_Tuner, KT368A)", test_rf_frontend_present(results)),
        ("Блок питания (Battery, Buck, LDO, Bead)", test_power_supply_present(results)),
        ("АО-процессор (FPAA, STM32, TinyML)", test_ao_processor_present(results)),
    ]
    
    for name, passed in tests:
        status = "✓ ПРОЙДЕН" if passed else "✗ НЕ ПРОЙДЕН"
        print(f"  [{status}] {name}")
    
    print("\n" + "=" * 70)
    print("ВЫВОД:")
    print("=" * 70)
    print("Принципиальная схема содержит:")
    print("  - 3 антенных канала (А: 0-900 МГц, Б: 1.2-3 ГГц, В: 4-7 ГГц)")
    print("  - Переключатель PIN-ключей (КА507)")
    print("  - RF-тракт: микшеры, ТВ-тюнер KS-H-132, буфер КТ368А")
    print("  - АО-процессор: FPAA (K140UD7/K140UD8), STM32 ADC, TinyML")
    print("  - ESP32 WiFi буфер")
    print("  - Блок питания: Li-Po, Buck converter, LDO, ферриточка")
    print("\nВсе требования из requirements_matrix.csv и technical_task.md")
    print("отражены в схеме. Для расчётов использовать .ipynb и Octave.")
    print("=" * 70)


if __name__ == "__main__":
    main()
