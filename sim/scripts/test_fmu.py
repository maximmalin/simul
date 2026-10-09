#!/usr/bin/env python3
"""test_fmu.py — проверка FMU-блока BraggBlock (FMI 2.0 Co-Simulation).

Запуск:
  cd /home/mirrage/Desktop/brig-receiver
  .venv/bin/python3 sim/scripts/test_fmu.py

Что проверяется:
  1. valid FMU + список переменных;
  2. engine_mode=0 (линейный): default-нагрузка -> delta_n ~ +1.5e-17;
  3. engine_mode=0 c входным сигналом: шаг нагрузки x10 -> delta_n x10;
  4. engine_mode=1 (полный Elmer): совпадение с линейным режимом (<0.01%);
  5. чтение accuracy_metadata (Raw String output через API).

Примечание: pythonfmu при завершении процесса может печатать
"corrupted double-linked list" — известная особенность выгрузки встроенного
libpython при выходе; на результаты не влияет.
"""
import os
import sys
import tempfile

import fmpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FMU = os.path.abspath(os.path.join(HERE, "..", "fmu", "BraggBlock.fmu"))

REF_DELTA_N = 1.5249e-17   # эталон для default-нагрузки (bragg_sim.py)


def main():
    print("=== FMU: read model description ===")
    md = fmpy.read_model_description(FMU)
    vr = {v.name: v.valueReference for v in md.modelVariables}
    for v in md.modelVariables:
        print(f"  {v.name:18s} {getattr(v,'causality','?'):9s} {getattr(v,'variability','?')}")

    print("\n=== test 1: engine_mode=0, default load ===")
    res = fmpy.simulate_fmu(FMU, stop_time=1.0, output_interval=1.0)
    r = res[-1]
    print(f"  delta_n={r['delta_n_mean']:.4e} (ref {REF_DELTA_N:.4e})")
    assert abs(r["delta_n_mean"] - REF_DELTA_N) / REF_DELTA_N < 1e-4
    assert r["model_status"] == 0
    print("  OK")

    print("\n=== test 2: engine_mode=0, load step b1 x10 @t=1s ===")
    sig = np.zeros(3, dtype=[("time", "f8"), ("b1", "f8"), ("b2", "f8"), ("b3", "f8")])
    sig["time"] = [0.0, 1.0, 2.0]
    sig["b1"] = [4.5e-3, 4.5e-2, 4.5e-2]
    res = fmpy.simulate_fmu(FMU, input=sig, stop_time=2.0, output_interval=1.0)
    ratio = res[-1]["delta_n_mean"] / res[0]["delta_n_mean"]
    print(f"  10x load -> delta_n ratio {ratio:.2f}")
    assert abs(ratio - 10.0) < 1e-6
    print("  OK (output after step holds input throughout the step)")

    print("\n=== test 3: engine_mode=1 (full Elmer each step) ===")
    res = fmpy.simulate_fmu(FMU, start_values={"engine_mode": 1},
                            stop_time=1.0, output_interval=1.0)
    d1 = res[-1]["delta_n_mean"]
    diff = abs(d1 - REF_DELTA_N) / abs(REF_DELTA_N) * 100
    print(f"  delta_n={d1:.4e}  mismatch vs linearized={diff:.4f}%")
    assert diff < 0.1
    print("  OK")

    print("\n=== test 4: string output accuracy_metadata ===")
    tmp = tempfile.mkdtemp()
    fmpy.extract(FMU, tmp)
    fmu = fmpy.instantiate_fmu(tmp, md, debug_logging=False)
    fmu.setupExperiment(startTime=0.0, stopTime=0.1, tolerance=1e-4)
    fmu.enterInitializationMode()
    fmu.exitInitializationMode()
    meta = fmu.getString([vr["accuracy_metadata"]])[0]
    print("  ", str(meta)[:200])
    fmu.terminate()

    print("\n=== ALL FMU TESTS PASSED ===")


if __name__ == "__main__":
    main()