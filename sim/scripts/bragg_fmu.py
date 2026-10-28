#!/usr/bin/env python3
"""bragg_fmu.py — FMI 2.0 Co-Simulation slave для блока «ячейка Брэгга».

Модуль 1: статическая линейная упругость (Elmer) → фотоупругость → Δn кварца.

Интерфейс FMU (FMI 2.0 Co-Simulation):
  Параметры (устанавливаются до инициализации):
    glass_E, glass_nu, quartz_E, quartz_nu   — материалы
    lx, ly, glass_lz, quartz_lz              — геометрия, м
    mesh_size                                — размер сетки, м
    dn_scale_quartz                          — Δn = dn_scale * ε_zz
    engine_mode                              — 0 = линейный (мгновенный),
                                                1 = полный пересчёт Elmer на шаге

  Входы (непрерывные):
    b1, b2, b3   — объёмная сила на кварце, Н/м³

  Выходы:
    delta_n_mean, eps_zz_mean, sigma_zz_mean, displacement_max,
    model_status (int: 0=ok, 2=error), accuracy_metadata (String, JSON)

Два движка:
  engine_mode = 0 (линейный): при инициализации решается 3 опорных случая
    (единичная сила по X/Y/Z), на каждом do_step — алгебраическая суперпозиция
    (мкс). Точно для ε_zz/σ_zz/Δn (линейные операторы от нагрузки);
    displacement_max — консервативная верхняя оценка Σ|b_i|·max|u_i|.
  engine_mode = 1 (полный): каждый do_step запускает bragg_sim.simulate()
    с текущей нагрузкой (~0.55 с/шаг на этой машине).

Сборка FMU:
  .venv/bin/python -m pythonfmu build -f sim/scripts/bragg_fmu.py -d sim/fmu
"""
import json
import os
import sys

from pythonfmu.fmi2slave import (
    Fmi2Causality,
    Fmi2Initial,
    Fmi2Slave,
    Fmi2Variability,
    Integer,
    Real,
    String,
)

# Работаем с реальным экземпляром проекта на этой машине.
_PROJECT = "/home/mirrage/Desktop/krystal"
_SCRIPTS = os.path.join(_PROJECT, "sim", "scripts")
os.environ["BRAGG_SIM_PROJECT"] = _PROJECT
for _p in (_SCRIPTS, _PROJECT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import bragg_sim  # noqa: E402


class BraggBlock(Fmi2Slave):
    """Ячейка Брэгга: механика (Elmer) → Δn кварца. FMI 2.0 Co-Simulation."""

    author = "Mirrage"
    description = "Bragg cell Module 1: elastic solution (Elmer) -> photoelastic delta_n of quartz"
    version = "1.0.0"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # ---------------- Параметры (до инициализации) ----------------
        self.glass_E = 70.0e9
        self.glass_nu = 0.22
        self.quartz_E = 78.5e9
        self.quartz_nu = 0.14
        self.lx = 15e-3
        self.ly = 5e-3
        self.glass_lz = 2.91e-3
        self.quartz_lz = 0.09e-3
        self.mesh_size = 1.5e-3
        self.dn_scale_quartz = -0.46
        self.engine_mode = 0  # 0 = линейный (мгновенный), 1 = полный Elmer

        # ---------------- Входы (непрерывные) ----------------
        self.b1 = 4.5e-3
        self.b2 = 0.0
        self.b3 = 0.0

        # ---------------- Выходы ----------------
        self.delta_n_mean = 0.0
        self.eps_zz_mean = 0.0
        self.sigma_zz_mean = 0.0
        self.displacement_max = 0.0
        self.model_status = 0
        self.accuracy_metadata = "{}"

        # ---------------- Внутреннее состояние ----------------
        self._ref = None          # опорные решения для линейного режима
        self._start_time = 0.0

        def reg(var):
            self.register_variable(var, nested=False)

        # параметры
        for name in ("glass_E", "glass_nu", "quartz_E", "quartz_nu",
                     "lx", "ly", "glass_lz", "quartz_lz", "mesh_size",
                     "dn_scale_quartz"):
            reg(Real(name, start=getattr(self, name),
                     causality=Fmi2Causality.parameter,
                     variability=Fmi2Variability.fixed,
                     initial=Fmi2Initial.exact))
        reg(Integer("engine_mode", start=0,
                    causality=Fmi2Causality.parameter,
                    variability=Fmi2Variability.fixed,
                    initial=Fmi2Initial.exact))

        # входы
        for name in ("b1", "b2", "b3"):
            reg(Real(name, start=getattr(self, name),
                     causality=Fmi2Causality.input,
                     variability=Fmi2Variability.continuous,
                     initial=Fmi2Initial.exact))

        # выходы (initial="calculated" -> без start в XML)
        for name in ("delta_n_mean", "eps_zz_mean", "sigma_zz_mean",
                     "displacement_max"):
            reg(Real(name,
                     causality=Fmi2Causality.output,
                     variability=Fmi2Variability.continuous,
                     initial=Fmi2Initial.calculated))
        reg(Integer("model_status",
                    causality=Fmi2Causality.output,
                    variability=Fmi2Variability.discrete,
                    initial=Fmi2Initial.calculated))
        reg(String("accuracy_metadata",
                   causality=Fmi2Causality.output,
                   variability=Fmi2Variability.discrete,
                   initial=Fmi2Initial.calculated))

    # ------------------------------------------------------------------ #
    def _base_params(self):
        return {
            "geometry": {
                "lx": self.lx, "ly": self.ly,
                "glass_lz": self.glass_lz, "quartz_lz": self.quartz_lz,
            },
            "mesh_size": self.mesh_size,
            "material": {
                "glass":  {"rho": 2500.0, "E": self.glass_E, "nu": self.glass_nu},
                "quartz": {"rho": 2650.0, "E": self.quartz_E, "nu": self.quartz_nu},
            },
            "optics": {"dn_scale_quartz": self.dn_scale_quartz},
        }

    def _solve(self, b1, b2, b3):
        """Полный расчёт Elmer с нагрузкой (b1,b2,b3)."""
        p = self._base_params()
        p["bodyforce"] = {"b1": b1, "b2": b2, "b3": b3}
        r = bragg_sim.simulate(p, write_json=False)
        return {
            "delta": r["quartz_layer"]["delta_n"]["mean"],
            "eps": r["quartz_layer"]["eps_zz"]["mean"],
            "sig": r["quartz_layer"]["sigma_zz_mean"],
            "disp": r["displacement_m"]["max_abs"],
        }

    def _apply(self, o):
        self.delta_n_mean = float(o["delta"])
        self.eps_zz_mean = float(o["eps"])
        self.sigma_zz_mean = float(o["sig"])
        self.displacement_max = float(o["disp"])

    def _metadata(self, n_solves, info=""):
        return json.dumps({
            "engine": "linear-superposition" if self.engine_mode == 0 else "full-elmer",
            "n_elmer_solves_at_init": n_solves,
            "mesh_size_m": self.mesh_size,
            "max_abs_note": (
                "linear mode: displacement_max = SUM|b_i|*max|u_i| (upper bound)" if self.engine_mode == 0
                else "full mode: exact max|u|"
            ),
            "info": info,
        }, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    def setup_experiment(self, start_time, stop_time, tolerance):
        self._start_time = float(start_time or 0.0)

    def enter_initialization_mode(self):
        self.model_status = 1
        if self.engine_mode == 0:
            # 3 опорных решения: единичная сила по каждой оси
            units = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
            self._ref = [self._solve(*u) for u in units]
            self.accuracy_metadata = self._metadata(3)
        else:
            self._ref = None
            self.accuracy_metadata = self._metadata(0)

    def exit_initialization_mode(self):
        try:
            if self.engine_mode == 0:
                self._rescale()
            else:
                self._apply(self._solve(self.b1, self.b2, self.b3))
            self.model_status = 0
        except Exception as exc:  # noqa: BLE001
            self.model_status = 2
            self.accuracy_metadata = json.dumps({"error": str(exc)})
            raise

    def do_step(self, current_time, step_size):
        try:
            if self.engine_mode == 0 and self._ref is not None:
                self._rescale()
            else:
                self._apply(self._solve(self.b1, self.b2, self.b3))
            self.model_status = 0
            return True
        except Exception:  # noqa: BLE001
            self.model_status = 2
            return False

    def _rescale(self):
        """Линейная суперпозиция опорных решений для текущих входов b."""
        o = {"delta": 0.0, "eps": 0.0, "sig": 0.0, "disp": 0.0}
        for b, ref in zip((self.b1, self.b2, self.b3), self._ref):
            o["delta"] += b * ref["delta"]
            o["eps"] += b * ref["eps"]
            o["sig"] += b * ref["sig"]
            o["disp"] += abs(b) * ref["disp"]  # верхняя оценка max|u|
        self._apply(o)

    def terminate(self):
        pass