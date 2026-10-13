#!/usr/bin/env python3
"""Smoke test: reproduce the reference openEMS patch tutorial."""
import os, sys, shutil, time, tempfile
from pathlib import Path
import numpy as np
os.environ["LD_LIBRARY_PATH"] = "/home/mirrage/opt/openEMS/lib:" + os.environ.get("LD_LIBRARY_PATH", "")
from CSXCAD import ContinuousStructure
from openEMS import openEMS
from openEMS.physical_constants import C0, EPS0, MUE0, Z0

Sim_Path = str(Path("sim/work/tutorial_patch").resolve())

# patch width (resonant length) in x-direction
patch_width  = 32   # mm
patch_length = 40   # mm

substrate_epsR   = 3.38
substrate_kappa  = 1e-3 * 2*np.pi*2.45e9 * EPS0*substrate_epsR
substrate_width  = 60
substrate_length = 60
substrate_thickness = 1.524
substrate_cells = 4

feed_pos = -6  # mm, in x
feed_R = 50

SimBox = np.array([200, 200, 150])  # mm

f0 = 2e9
fc = 1e9

FDTD = openEMS(NrTS=30000, EndCriteria=1e-4)
FDTD.SetGaussExcite(f0, fc)
FDTD.SetBoundaryCond(['MUR','MUR','MUR','MUR','MUR','MUR'])

CSX = ContinuousStructure()
FDTD.SetCSX(CSX)
mesh = CSX.GetGrid()
mesh.SetDeltaUnit(1e-3)  # mm
mesh_res = C0/(f0+fc)/1e-3/20

mesh.AddLine('x', [-SimBox[0]/2, SimBox[0]/2])
mesh.AddLine('y', [-SimBox[1]/2, SimBox[1]/2])
mesh.AddLine('z', [-SimBox[2]/3, SimBox[2]*2/3])

patch = CSX.AddMetal('patch')
start = [-patch_width/2, -patch_length/2, substrate_thickness]
stop  = [ patch_width/2 , patch_length/2, substrate_thickness]
patch.AddBox(priority=10, start=start, stop=stop)
FDTD.AddEdges2Grid(dirs='xy', properties=patch, metal_edge_res=mesh_res/2)

substrate = CSX.AddMaterial('substrate', epsilon=substrate_epsR, kappa=substrate_kappa)
start = [-substrate_width/2, -substrate_length/2, 0]
stop  = [ substrate_width/2,  substrate_length/2, substrate_thickness]
substrate.AddBox(priority=0, start=start, stop=stop)
mesh.AddLine('z', np.linspace(0, substrate_thickness, substrate_cells+1))

gnd = CSX.AddMetal('gnd')
start = [-substrate_width/2, -substrate_length/2, 0]
stop  = [ substrate_width/2,  substrate_length/2, 0]
gnd.AddBox(start, stop, priority=10)
FDTD.AddEdges2Grid(dirs='xy', properties=gnd)

start = [feed_pos, 0, 0]
stop  = [feed_pos, 0, substrate_thickness]
port = FDTD.AddLumpedPort(1, feed_R, start, stop, 'z', 1.0, priority=5, edges2grid='xy')

mesh.SmoothMeshLines('all', mesh_res, 1.4)

if Sim_Path and os.path.exists(Sim_Path):
    shutil.rmtree(Sim_Path)
os.makedirs(Sim_Path, exist_ok=True)

t0=time.time()
FDTD.Run(Sim_Path, cleanup=True)
print(f"FDTD done {time.time()-t0:.0f}s")

f = np.linspace(max(1e9, f0-fc), f0+fc, 401)
port.CalcPort(Sim_Path, f)
s11 = port.uf_ref/port.uf_inc
s11_dB = 20.0*np.log10(np.abs(s11))
ires = np.argmin(s11_dB)
print(f"f_res={f[ires]/1e9:.3f} GHz  S11={s11_dB[ires]:.1f} dB")
i2 = np.argmin(np.abs(f-2e9))
print(f"S11 at 2.0GHz: {s11_dB[i2]:.1f} dB")
Zin = port.uf_tot/port.if_tot
print(f"Zin at res: {np.real(Zin[ires]):.0f}+j{np.imag(Zin[ires]):.0f}")
print("S11 min over band:", np.min(s11_dB))