#!/usr/bin/env python3
"""bragg_sim.py — параметрический расчёт ячейки Брэгга: Gmsh → Elmer → JSON.

ОДНА точка входа, ОДИН выходной файл.

Структура проекта:

    PROJECT/                      (Desktop/krystal)
    ├── sim/
    │   ├── README.md             ← как пользоваться (читайте первым)
    │   ├── inputs/
    │   │   └── bragg_inputs.yaml ← ВХОДНЫЕ ПАРАМЕТРЫ (правите этот файл)
    │   ├── outputs/
    │   │   └── elmer_results.json← РЕЗУЛЬТАТ (один выходной файл)
    │   ├── fmu/
    │   │   └── BraggBlock.fmu    ← ко-симуляционный блок FMI 2.0
    │   ├── scripts/
    │   │   ├── bragg_sim.py      ← этот скрипт
    │   │   ├── msh_to_elmer.py   ← конвертер .msh → Elmer
    │   │   ├── bragg_fmu.py      ← исходник FMU (BraggBlock)
    │   │   └── test_fmu.py       ← проверка FMU через FMPy
    │   └── work/                 ← промежуточные файлы (авто, не править)
    │       ├── bragg.geo         ← параметрическая геометрия Gmsh
    │       ├── case.sif          ← собранный SIF для Elmer
    │       ├── elmersolver.log   ← лог решателя
    │       └── bragg_cell/       ← сетка Elmer + результат .vtu
    └── ...

Запуск (из корня проекта):

    .venv/bin/python3 sim/scripts/bragg_sim.py

Параметры берутся из sim/inputs/bragg_inputs.yaml (файл создаётся
автоматически при первом запуске, если его нет). Результат пишется в
sim/outputs/elmer_results.json.

Функция simulate(params) возвращает словарь результатов — её можно
вызывать повторно (в т.ч. в цикле «реального времени», когда блоку нужно
принимать входные параметры и отдавать выходные).

Про «реальное время»:
  * сетка кешируется по сигнатуре геометрии — при неизменной геометрии
    (меняются только материалы/нагрузка) перерасчёт ~1 с (solver 0.4-0.7 с);
  * при изменении геометрии перестроение сетки занимает ~0.1 с (gmsh);
  * нагрузка для линейной упругости входит линейно: можно посчитать один раз
    опорное решение и масштабировать результат под любую нагрузку мгновенно —
    именно так работает «быстрый» режим FMU (linearized).
"""

import hashlib
import json
import math  # noqa: F401
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import msh_to_elmer  # noqa: E402


def _default_project():
    """Корень проекта: Desktop/krystal (родитель папки sim/)."""
    if _HERE.parent.name == "sim" and (_HERE.parent.parent / "sim").is_dir():
        return _HERE.parent.parent
    # Совместимость: скрипт, лежащий прямо в корне проекта.
    return _HERE


# Корень проекта: по умолчанию — родитель папки sim/, но может быть
# переопределён через BRAGG_SIM_PROJECT (так делает FMU-обёртка, чтобы
# всегда работать с одним и тем же экземпляром проекта).
_PROJECT = Path(os.environ.get("BRAGG_SIM_PROJECT", str(_default_project())))


# ============================================================================
# ВХОДНЫЕ ПАРАМЕТРЫ — значения по умолчанию. Актуальные значения читаются из
# sim/inputs/bragg_inputs.yaml (если файла нет — создаётся автоматически).
# ============================================================================
INPUT_PARAMS = {
    # --- Геометрия, м ---
    "geometry": {
        "lx": 15e-3,            # длина по X
        "ly": 5e-3,             # ширина по Y
        "glass_lz": 2.91e-3,    # высота стекла (низ блока)
        "quartz_lz": 0.09e-3,   # толщина кварца (сверху)
    },
    "mesh_size": 1.5e-3,        # характерный размер сетки, м

    # --- Материалы ---
    "material": {
        "glass":  {"rho": 2500.0, "E": 70.0e9, "nu": 0.22},
        "quartz": {"rho": 2650.0, "E": 78.5e9, "nu": 0.14},
    },

    # --- Нагрузка: объёмная сила на кварце, Н/м³ ---
    "bodyforce": {"b1": 4.5e-3, "b2": 0.0, "b3": 0.0},

    # --- Оптика ---
    "optics": {"dn_scale_quartz": -0.46},   # Δn = dn_scale * ε_zz (кварц)

    # --- Коды BC из mesh.boundary (x≈0 → front, z≈0 → bottom) ---
    "bc": {"front_code": 1, "bottom_code": 5},

    # --- Пути и исполняемые ---
    "gmsh_bin": "/usr/bin/gmsh",
    "elmer_solver": "/home/mirrage/Desktop/elmer_installed/bin/ElmerSolver_mpi",
    "config_file": str(_PROJECT / "sim" / "inputs" / "bragg_inputs.yaml"),
    "run_dir": str(_PROJECT / "sim" / "work"),
    "mesh_dir": str(_PROJECT / "sim" / "work" / "bragg_cell"),
    "msh_file": str(_PROJECT / "sim" / "work" / "bragg_cell" / "bragg_cell.msh"),
    "sif_name": "case.sif",
    "out_json": str(_PROJECT / "sim" / "outputs" / "elmer_results.json"),
}

# Значения по умолчанию для нового файла bragg_inputs.yaml
_CONFIG_TEMPLATE = """\
# ============================================================================
# ВХОДНЫЕ ПАРАМЕТРЫ ЯЧЕЙКИ БРЭГГА  (sim/inputs/bragg_inputs.yaml)
# ============================================================================
# Правите ЭТОТ файл и запускаете:
#     .venv/bin/python3 sim/scripts/bragg_sim.py
# Результат: sim/outputs/elmer_results.json
# Все величины в СИ (м, Н/м³, Па).

geometry:
  lx: 0.015          # длина по X, м
  ly: 0.005          # ширина по Y, м
  glass_lz: 0.00291  # высота стекла (низ блока), м
  quartz_lz: 0.00009 # толщина кварца (сверху), м

mesh_size: 0.0015    # характерный размер сетки, м

material:
  glass:
    rho: 2500.0      # плотность, кг/м³
    E: 7.0e10        # модуль Юнга, Па
    nu: 0.22         # коэффициент Пуассона
  quartz:
    rho: 2650.0
    E: 7.85e10
    nu: 0.14

bodyforce:           # объёмная сила на кварце, Н/м³
  b1: 0.0045
  b2: 0.0
  b3: 0.0

optics:
  dn_scale_quartz: -0.46   # Δn = dn_scale * ε_zz

bc:
  front_code: 1
  bottom_code: 5

# Пути (в обычном случае не меняйте)
gmsh_bin: /usr/bin/gmsh
elmer_solver: /home/mirrage/Desktop/elmer_installed/bin/ElmerSolver_mpi
"""


# ============================================================================
# Служебные функции
# ============================================================================
def _fmt(x):
    return f"{float(x):.9g}"


def _deep_merge(base, override):
    """Сливает словари на всю глубину; override перекрывает base."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _sig(params):
    """Сигнатура геометрии+сетки (для кеша mesh)."""
    g = params["geometry"]
    raw = (g["lx"], g["ly"], g["glass_lz"], g["quartz_lz"],
           params["mesh_size"])
    return hashlib.sha256(
        "|".join(f"{float(x):.12e}" for x in raw).encode()
    ).hexdigest()[:16]


def ensure_config():
    """Создаёт sim/inputs/bragg_inputs.yaml, если его ещё нет."""
    cfg = Path(INPUT_PARAMS["config_file"])
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(_CONFIG_TEMPLATE)
        print(f"[cfg]   создан {cfg} — правьте его под свою задачу")
    return cfg


def _coerce_numeric(v):
    """Переводит строки-числа ('7.85e10') в float/int — на всякий случай,
    даже если yaml-парсер отдал их как строки."""
    if isinstance(v, dict):
        return {k: _coerce_numeric(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_coerce_numeric(x) for x in v]
    if isinstance(v, str):
        s = v.strip()
        try:
            return int(s)
        except ValueError:
            pass
        try:
            return float(s)
        except ValueError:
            return v
    return v


def load_config():
    """Читает bragg_inputs.yaml; возвращает словарь (пустой, если нет yaml)."""
    cfg_path = Path(INPUT_PARAMS["config_file"])
    if not cfg_path.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(cfg_path.read_text()) or {}
        if not isinstance(data, dict):
            print(f"[warn]  {cfg_path} не содержит словаря — игнорирую")
            return {}
        return _coerce_numeric(data)
    except ImportError:
        print("[warn]  модуль yaml недоступен — используются значения "
              "INPUT_PARAMS по умолчанию")
        return {}
    except Exception as e:
        print(f"[warn]  не могу прочитать {cfg_path}: {e}")
        return {}


# ============================================================================
# 1. Gmsh: параметрическая геометрия → .msh
# ============================================================================
def write_geo(params, geo_path):
    g = params["geometry"]
    geo = f"""SetFactory("OpenCASCADE");
Lx = {_fmt(g['lx'])};
Ly = {_fmt(g['ly'])};
Hzg = {_fmt(g['glass_lz'])};
Hzq = {_fmt(g['quartz_lz'])};
ms = {_fmt(params['mesh_size'])};      // 'MeshSize' — зарезервированное слово в .geo
Box(1) = {{0,0,0, Lx,Ly,Hzg}};
Box(2) = {{0,0,Hzg, Lx,Ly,Hzq}};
Physical Volume(1) = {{1}};
Physical Volume(2) = {{2}};
Mesh.MeshSizeMax = ms;
"""
    Path(geo_path).write_text(geo)
    return geo_path


def run_gmsh(params):
    """Генерирует .msh по параметрам. Возвращает путь к .msh."""
    run_dir = Path(params["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    geo = str(run_dir / "bragg.geo")
    msh = str(params["msh_file"])
    Path(msh).parent.mkdir(parents=True, exist_ok=True)
    write_geo(params, geo)
    r = subprocess.run(
        [params["gmsh_bin"], geo, "-3", "-o", msh],
        cwd=str(run_dir), capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError("gmsh не смог построить сетку:\n" + r.stderr[-2000:])
    return msh


# ============================================================================
# 2. Elmer-сетка (кеш по геометрии)
# ============================================================================
def build_mesh(params, force=False):
    """Elmer mesh из .msh. Кешируется по сигнатуре геометрии."""
    mesh_dir = Path(params["mesh_dir"])
    marker = mesh_dir / ".mesh_signature.json"
    sig = _sig(params)
    fresh = False

    if not force and marker.exists():
        try:
            old = json.loads(marker.read_text())["signature"]
            fresh = old == sig and (mesh_dir / "mesh.header").exists()
        except Exception:
            fresh = False

    if not fresh:
        t0 = time.time()
        run_gmsh(params)      # всегда перестраиваем .msh из текущих параметров
        mesh_dir.mkdir(parents=True, exist_ok=True)
        msh_to_elmer.convert(params["msh_file"], str(mesh_dir),
                             bc_front_code=params["bc"]["front_code"],
                             bc_bottom_code=params["bc"]["bottom_code"])
        marker.write_text(json.dumps({"signature": sig}))
        print(f"[mesh] перестроена за {time.time()-t0:.2f} с")
    else:
        print("[mesh] используется кеш (геометрия не менялась)")
    return mesh_dir


# ============================================================================
# 3. SIF из параметров
# ============================================================================
def write_sif(params):
    g = params["geometry"]
    m = params["material"]
    bf = params["bodyforce"]
    bc = params["bc"]
    run_dir = Path(params["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    sif_path = run_dir / params["sif_name"]

    sif = f"""! Bragg cell SIF — сгенерировано bragg_sim.py
Header
  CHECK KEYWORDS Warn
  Mesh DB "{os.path.basename(str(params['mesh_dir']))}"
  Include Path "."
End

Simulation
  Coordinate Mapping(3) = 1 2 3
  Coordinate System = "Cartesian"
  Simulation Type = "Steady State"
  Steady State Max Iterations = 1
  Steady State Min Iterations = 0
  Use Mesh Names = True
End

Equation 1
  Name = "SolidMechanics"
  Active Solvers(1) = 1
End

Solver 1
  Equation = "StressSolve"
  Procedure = "StressSolve" "StressSolver"
  Variable = "displacement"
  Variable DOFs = 3
  Calculate Stresses = True
  Linear System Solver = "Direct"
  Linear System Direct Method = "Umfpack"
  Stabilize = True
  Steady State Convergence Tolerance = 1.0e-6
End

Solver 2
  Equation = "ResultOutput"
  Procedure = "ResultOutputSolve" "ResultOutputSolver"
  Output File Name = "bragg_results"
  Vtu Format = True
  Binary Output = False
  Exec Solver = "After simulation"
End

Material 1
  Name = "Glass"
  Density = {_fmt(m['glass']['rho'])}
  Youngs modulus = {_fmt(m['glass']['E'])}
  Poisson ratio = {_fmt(m['glass']['nu'])}
End

Material 2
  Name = "Quartz"
  Density = {_fmt(m['quartz']['rho'])}
  Youngs modulus = {_fmt(m['quartz']['E'])}
  Poisson ratio = {_fmt(m['quartz']['nu'])}
End

Body 1
  Target Bodies(1) = 1
  Name = "glass_body"
  Equation = 1
  Material = 1
End

Body 2
  Target Bodies(1) = 2
  Name = "quartz_body"
  Equation = 1
  Material = 2
  Body Force = 1
End

Body Force 1
  Stress Bodyforce 1 = {_fmt(bf['b1'])}
  Stress Bodyforce 2 = {_fmt(bf['b2'])}
  Stress Bodyforce 3 = {_fmt(bf['b3'])}
End

Boundary Condition 1
  Name = "FrontFixed"
  Target Boundaries(1) = {bc['front_code']}
  Displacement 1 = 0.0
  Displacement 2 = 0.0
  Displacement 3 = 0.0
End

Boundary Condition 2
  Name = "BottomFixed"
  Target Boundaries(1) = {bc['bottom_code']}
  Displacement 1 = 0.0
  Displacement 2 = 0.0
  Displacement 3 = 0.0
End
"""
    sif_path.write_text(sif)
    # ELMERSOLVER_STARTINFO — на случай запуска без аргументов
    (run_dir / "ELMERSOLVER_STARTINFO").write_text(
        f"{os.path.basename(str(sif_path))}\n")
    print(f"[sif]   {sif_path}")
    return sif_path


# ============================================================================
# 4. ElmerSolver
# ============================================================================
def run_solver(params):
    run_dir = Path(params["run_dir"])
    sif = params["sif_name"]
    log_path = run_dir / "elmersolver.log"
    t0 = time.time()
    with open(log_path, "w") as log:
        r = subprocess.run(
            [params["elmer_solver"], sif],
            cwd=str(run_dir),
            stdout=log, stderr=subprocess.STDOUT,
        )
    dts = time.time() - t0
    print(f"[solve] rc={r.returncode} за {dts:.2f} с (лог: {log_path})")
    if r.returncode != 0:
        tail = "\n".join(log_path.read_text().splitlines()[-40:])
        raise RuntimeError(f"ElmerSolver rc={r.returncode}:\n{tail}")
    return {"returncode": r.returncode, "elapsed_s": round(dts, 3)}


# ============================================================================
# 5. Извлечение результатов из .vtu
# ============================================================================
def read_vtu_fields(vtu_path):
    """Читает именованные PointData (ASCII) и Points из VTK XML vtu."""
    import re
    content = Path(vtu_path).read_text()
    data = {}
    pat = re.compile(
        r'<DataArray\s+type="Float64"\s+Name="([^"]+)"\s+'
        r'NumberOfComponents="(\d+)"\s+format="ascii">\s*(.*?)\s*'
        r'</DataArray>', re.DOTALL)
    for name, ncomp, text in pat.findall(content):
        arr = np.fromstring(text, sep=" ")
        if int(ncomp) > 1:
            arr = arr.reshape(-1, int(ncomp))
        data[name] = arr

    pm = re.search(
        r'<Points>\s*<DataArray\s+type="Float64"\s+NumberOfComponents="(\d+)"'
        r'\s+format="ascii">\s*(.*?)\s*</DataArray>\s*</Points>',
        content, re.DOTALL)
    if pm:
        arr = np.fromstring(pm.group(2), sep=" ")
        data["Points"] = arr.reshape(-1, int(pm.group(1)))

    def get(name):
        for k, v in data.items():
            if k.lower() == name.lower():
                return v
        raise KeyError(f"поле '{name}' не найдено в vtu; есть: {sorted(data)}")

    return data, get


def extract_results(params, vtu_path):
    """Считает ε_zz и Δn по слоям, возвращает словарь результатов."""
    data, get = read_vtu_fields(vtu_path)
    coords = data["Points"]
    disp = get("displacement")
    szz = get("stress_zz")
    sxx = get("stress_xx")
    syy = get("stress_yy")

    mq = params["material"]["quartz"]
    mg = params["material"]["glass"]
    dn_scale = params["optics"]["dn_scale_quartz"]

    glass_top = params["geometry"]["glass_lz"]
    mask_q = coords[:, 2] >= glass_top
    mask_g = ~mask_q

    def stats(a):
        return {"mean": float(a.mean()), "max": float(a.max()),
                "min": float(a.min())}

    eps_q = (szz[mask_q] - mq["nu"] * (sxx[mask_q] + syy[mask_q])) / mq["E"]
    eps_g = (szz[mask_g] - mg["nu"] * (sxx[mask_g] + syy[mask_g])) / mg["E"]

    results = {
        "n_nodes": int(len(coords)),
        "displacement_m": {"max_abs": float(np.max(np.abs(disp)))},
        "quartz_layer": {
            "n_nodes": int(mask_q.sum()),
            "sigma_zz_mean": float(szz[mask_q].mean()),
            "eps_zz": stats(eps_q),
            "delta_n": {k: float(dn_scale * v) for k, v in stats(eps_q).items()},
        },
        "glass_layer": {
            "n_nodes": int(mask_g.sum()),
            "sigma_zz_mean": float(szz[mask_g].mean()),
            "eps_zz": stats(eps_g),
        },
    }
    # ---- Photoacoustic mapping (структурная схема Отсек 2) ----
    # В структурной схеме: Электричество → кварц (триансудатор) → упругие волны в PMMA
    # → дифракция лазера 650 нм (угол 0.524°) → фотодиодный ток 100 нА–600 мкА.
    # Наш блок вычисляет Δn от механического напряжения — это базис фотоакустики.
    # Добавляем estimées параметры, описывающие состояние фазо-сдвига и
    # нормализованный сигнал для фото-детектора.
    import math
    lam_laser = 0.65e-6  # длина волны лазера из схемы (650 нм)
    d_quartz = params["geometry"]["glass_lz"] + params["geometry"]["quartz_lz"]  # общая толщина кварцевого блока
    dn_mean = results["quartz_layer"]["delta_n"]["mean"]
    # Фазовый сдвиг, который возник бы при прохождении лазера через деформированный кварц:
    phase_shift_rad = 2 * math.pi * abs(dn_mean) * d_quartz / lam_laser
    # Эквивалентный нормализованный сигнал фото-детектора (апостериорная шкала)
    eq_photocurrent_ua = abs(dn_mean) * 1e6  # от 0 до ~100 µA при starken деформациях
    results["photoacoustic"] = {
        "delta_n_mean": dn_mean,
        "phase_shift_rad": round(phase_shift_rad, 6),
        "equivalent_photocurrent_ua": round(eq_photocurrent_ua, 3),
        "note": "These fields model the 'strain→Δn→photoacoustic signal' link. " \
          "Full diffraction/photodiode cycle: see structural scheme Sect. 2."
    }
    return results


# ============================================================================
# 6. simulate() — полный прогон; возвращает результаты и пишет JSON
# ============================================================================
def simulate(params=None, force_rebuild=False, write_json=True):
    """Полный прогон: mesh → sif → solver → results.

    params: словарь ПОВЕРХ bragg_inputs.yaml и INPUT_PARAMS (вложенные
            словари сливаются на всю глубину).
    force_rebuild: пересобрать сетку даже при неизменной геометрии.
    write_json: писать params['out_json'] с результатами.
    Возвращает словарь результатов.
    """
    p = _deep_merge(INPUT_PARAMS, load_config())
    p = _deep_merge(p, params or {})
    t_start = time.time()

    build_mesh(p, force=force_rebuild)
    write_sif(p)
    solver_info = run_solver(p)

    mesh_dir = Path(p["mesh_dir"])
    vtu = next(mesh_dir.glob("bragg_results_t0001.vtu"), None)
    if vtu is None:
        raise RuntimeError(f"нет .vtu в {mesh_dir}")
    results = extract_results(p, vtu)
    results["solver"] = solver_info
    results["vtu"] = str(vtu)
    results["total_elapsed_s"] = round(time.time() - t_start, 3)
    results["params_used"] = {
        "geometry": p["geometry"],
        "mesh_size": p["mesh_size"],
        "material": {
            k: {"rho": v["rho"], "E": v["E"], "nu": v["nu"]}
            for k, v in p["material"].items()
        },
        "bodyforce": p["bodyforce"],
        "optics": p["optics"],
    }

    if write_json:
        out = Path(p["out_json"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(results, indent=2))
        print(f"[out]   {out}")

    print(f"[done]  полный прогон {results['total_elapsed_s']:.2f} с")
    return results


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    ensure_config()
    simulate()


if __name__ == "__main__":
    main()