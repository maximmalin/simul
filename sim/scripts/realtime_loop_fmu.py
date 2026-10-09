#!/usr/bin/env python3
"""realtime_loop_fmu.py — пример петли «реального времени» на FMU BraggBlock.

Демонстрация того, что FMU можно гонять в цикле реального времени:
вход b1 меняется на каждом шаге (здесь — по синусу), выход delta_n снимается
после каждого шага. Режим engine_mode=0 (linearized): после инициализации
(3 опорных решения Elmer ~1.8 с) каждый шаг занимает микросекунды —
подходит для RT-циклов при частотах до сотен Гц.

Запуск:
    cd /home/mirrage/Desktop/brig-receiver
    .venv/bin/python3 sim/scripts/realtime_loop_fmu.py

Семантика шага FMI co-sim: вход удерживается постоянным на протяжении шага,
выход отражает его в конце шага.
"""
import math
import os
import tempfile
import time

import fmpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FMU = os.path.abspath(os.path.join(HERE, "..", "fmu", "BraggBlock.fmu"))

DT = 0.1          # шаг цикла, с
N_STEPS = 50      # число шагов цикла
B1_MEAN = 4.5e-3  # средняя нагрузка, Н/м³
FREQ = 0.5        # частота изменения входа, Гц


def main():
    md = fmpy.read_model_description(FMU)
    vr = {v.name: v.valueReference for v in md.modelVariables}

    tmp = tempfile.mkdtemp()
    fmpy.extract(FMU, tmp)
    fmu = fmpy.instantiate_fmu(tmp, md, debug_logging=False)

    # engine_mode = 0 ставится ДО инициализации
    fmu.setInteger([vr["engine_mode"]], [0])
    fmu.setupExperiment(startTime=0.0, stopTime=N_STEPS * DT, tolerance=1e-4)
    t0 = time.perf_counter()
    fmu.enterInitializationMode()    # здесь: 3 опорных решения Elmer (~1.6 с)
    fmu.exitInitializationMode()
    t_init = time.perf_counter() - t0

    out = []
    t_step = 0.0
    t_wall = time.perf_counter()
    for k in range(N_STEPS):
        t = k * DT
        b1 = B1_MEAN * (1.0 + 0.5 * math.sin(2 * math.pi * FREQ * t))
        t0 = time.perf_counter()
        fmu.setReal([vr["b1"], vr["b2"], vr["b3"]], [b1, 0.0, 0.0])
        fmu.doStep(currentCommunicationPoint=t, communicationStepSize=DT)
        dn = fmu.getReal([vr["delta_n_mean"]])[0]
        eps = fmu.getReal([vr["eps_zz_mean"]])[0]
        out.append((t, b1, eps, dn))
        t_step += time.perf_counter() - t0
    t_loop = time.perf_counter() - t_wall

    fmu.terminate()

    print(f"init (3 Elmer solves): {t_init:.2f} с")
    print(f"цикл {N_STEPS} шагов:    {t_loop:.3f} с,  "
          f"~{t_step / N_STEPS * 1e6:.1f} мкс на шаг (модель), "
          f"период RT = {DT * 1000:.0f} мс")
    print(f"\n  t(с)      b1(Н/м³)     eps_zz_mean      delta_n_mean")
    for i in range(0, N_STEPS, 5):
        t, b1, eps, dn = out[i]
        print(f"  {t:5.1f}  {b1:12.4g}  {eps:15.4e}  {dn:15.4e}")
    print("\nВывод: FMU (engine_mode=0) позволяет цикл реального времени — "
          "стоимость шага << периода RT. Для engine_mode=1 (полный Elmer) "
          "минимальный период ~0.6-0.7 с (0.55 с/шаг на этой машине).")


if __name__ == "__main__":
    main()