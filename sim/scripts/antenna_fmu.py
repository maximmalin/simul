#!/usr/bin/env python3
"""FMI 2.0 Co-Simulation: тестовая заглушка трёх антенных каналов.

Табличные S11/efficiency НЕ получены из EM-расчёта или измерений.
Весовые коэффициенты b1/b2 сохранены только для тестирования интерфейса.
b3 — скорость изменения частоты в Гц/с (не МГц за вызов do_step).
Подробности: sim/fmu/README_antenna.md.
"""

from pythonfmu.fmi2slave import (
    Fmi2Causality,
    Fmi2Initial,
    Fmi2Slave,
    Fmi2Variability,
    Integer,
    Real,
    String,
)
import math
from xml.etree.ElementTree import SubElement


class AntennaBlock(Fmi2Slave):
    """Тестовая заглушка; не электромагнитная модель антенны."""

    author = "Mirrage"
    description = "UNVALIDATED antenna interface stub; hard-coded demo characteristics, not EM results"
    version = "1.1.0"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.antenna_mode = 0  # дискретный вход: A / B / C
        self.b1 = 1.0  # демонстрационный вес S11 и efficiency
        self.b2 = 0.0  # дополнительный демонстрационный вес efficiency
        self.b3 = 0.0  # дрейф, Гц/с
        self.initial_frequency = 400e6  # параметр, Гц
        self.s11_db = -10.0
        self.efficiency = 0.65
        self.frequency = self.initial_frequency
        self.status = 1  # 1 = unvalidated stub / warning, 2 = error
        self.status_message = "UNVALIDATED STUB: no EM calculation or measurements"
        self.s11_ref = [-10.0, -15.0, -12.0]
        self.efficiency_ref = [0.65, 0.72, 0.68]
        self._bands = [(0.0, 900e6), (1.2e9, 3e9), (4e9, 7e9)]

        self.register_variable(Integer(
            "antenna_mode", causality=Fmi2Causality.input,
            variability=Fmi2Variability.discrete))
        for name in ("b1", "b2", "b3"):
            self.register_variable(Real(
                name, causality=Fmi2Causality.input,
                variability=Fmi2Variability.continuous))
        self.register_variable(Real(
            "initial_frequency", causality=Fmi2Causality.parameter,
            variability=Fmi2Variability.fixed, initial=Fmi2Initial.exact))
        for name in ("s11_db", "efficiency", "frequency"):
            self.register_variable(Real(
                name, causality=Fmi2Causality.output,
                variability=Fmi2Variability.continuous,
                initial=Fmi2Initial.calculated))
        for cls, name in ((Integer, "status"), (String, "status_message")):
            self.register_variable(cls(
                name, causality=Fmi2Causality.output,
                variability=Fmi2Variability.discrete,
                initial=Fmi2Initial.calculated))

    def to_xml(self, model_options=None):
        # PythonFMU 0.7 omits InitialUnknowns for calculated outputs.
        root = super().to_xml(model_options or {})
        structure = root.find("ModelStructure")
        if structure.find("InitialUnknowns") is None:
            unknowns = SubElement(structure, "InitialUnknowns")
            for index, variable in enumerate(self.vars.values(), 1):
                if (variable.causality == Fmi2Causality.output
                        and variable.initial == Fmi2Initial.calculated):
                    SubElement(unknowns, "Unknown", index=str(index))
        return root

    def exit_initialization_mode(self):
        try:
            self._update(self.initial_frequency)
        except ValueError as exc:
            self._error(exc)
            raise

    def _error(self, exc):
        self.status = 2
        self.status_message = str(exc)

    def _update(self, frequency):
        """Демонстрационная арифметика, НЕ суперпозиция физических S-параметров."""
        if self.antenna_mode not in (0, 1, 2):
            raise ValueError("antenna_mode must be 0, 1 or 2")
        if not all(math.isfinite(v) for v in (self.b1, self.b2, self.b3, frequency)):
            raise ValueError("inputs and frequency must be finite")
        if not (0 <= self.b1 <= 1 and 0 <= self.b2 <= 1):
            raise ValueError("demo weights b1 and b2 must be in [0, 1]")
        if not 0 < frequency <= 7e9:
            raise ValueError("frequency must be in (0, 7e9] Hz")
        mode = int(self.antenna_mode)
        efficiency = self.efficiency_ref[mode] * (self.b1 + self.b2)
        if efficiency > 1:
            raise ValueError("demo weights produce efficiency > 1")
        self.s11_db = self.s11_ref[mode] * self.b1
        self.efficiency = efficiency
        self.frequency = frequency
        self.status = 1
        self.status_message = "UNVALIDATED STUB: no EM calculation or measurements"
        low, high = self._bands[mode]
        if not low <= frequency <= high:
            self.status_message += "; frequency outside selected nominal band"

    def do_step(self, current_time, step_size):
        try:
            if not (math.isfinite(current_time) and math.isfinite(step_size) and step_size > 0):
                raise ValueError("time must be finite and step_size must be positive")
            self._update(self.frequency + self.b3 * step_size)
            return True
        except ValueError as exc:
            self._error(exc)
            return False


if __name__ == "__main__":
    print("Build: .venv/bin/python -m pythonfmu build -f sim/scripts/antenna_fmu.py -d sim/fmu")