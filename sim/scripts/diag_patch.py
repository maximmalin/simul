#!/usr/bin/env python3
"""Diagnostic: patch vs no-patch - does the port couple to the antenna?"""
import os, sys, shutil, time
from pathlib import Path
import numpy as np
os.environ["LD_LIBRARY_PATH"] = "/home/mirrage/opt/openEMS/lib:" + os.environ.get("LD_LIBRARY_PATH", "")
from CSXCAD import ContinuousStructure
from openEMS import openEMS

C0 = 299792458.0; EPS0 = 8.854187817e-12; EPSR = 4.5; TAND = 0.02; H_SUB = 0.8e-3; PML = 8
WORK = Path("sim/work").resolve()

def calc_patch(f0):
    W = C0/(2*f0)*np.sqrt(2/(EPSR+1))
    eps_eff=(EPSR+1)/2+(EPSR-1)/2*(1+12*H_SUB/W)**(-0.5)
    dL=0.412*H_SUB*(eps_eff+0.3)/(eps_eff-0.258)*(W/H_SUB+0.264)/(W/H_SUB+0.813)
    L=C0/(2*f0*np.sqrt(eps_eff))-2*dL
    return W,L,eps_eff

def snap(v,g): return g[np.argmin(np.abs(g-v))]

def ugrid(size,step,h_sub=H_SUB):
    inner=np.arange(0,size+step*0.5,step); inner=np.unique(np.append(inner,h_sub))
    if inner[-1]<size-1e-12: inner=np.append(inner,size)
    return np.unique(inner)

def run(f0, with_patch, dirname, simtime=200000, endcrit=1e-6):
    W,L,e = calc_patch(f0)
    margin=15e-3; zs=25e-3; step=2e-3
    xs=W+2*margin; ys=L+2*margin
    xs_arr=ugrid(xs,step); ys_arr=ugrid(ys,step); zs_arr=ugrid(zs,step,H_SUB)
    H_SUB_s=snap(H_SUB,zs_arr); z_top=zs_arr[zs_arr>H_SUB_s][0]
    cx,cy=xs/2,ys/2
    px1=snap(cx-W/2,xs_arr); py1=snap(cy-L/2,ys_arr); px2=snap(cx+W/2,xs_arr); py2=snap(cy+L/2,ys_arr)
    aW,aL=px2-px1,py2-py1
    feed_x=snap(px1+aW/2,xs_arr); feed_y=snap(py1+aL*0.33,ys_arr)

    CSX=ContinuousStructure(); g=CSX.GetGrid(); g.SetDeltaUnit(1)
    g.AddLine("x",np.concatenate([[-step*i for i in range(PML,0,-1)],xs_arr,[xs+step*i for i in range(1,PML+1)]]))
    g.AddLine("y",np.concatenate([[-step*i for i in range(PML,0,-1)],ys_arr,[ys+step*i for i in range(1,PML+1)]]))
    g.AddLine("z",np.concatenate([[-step*i for i in range(PML,0,-1)],zs_arr,[zs+step*i for i in range(1,PML+1)]]))

    mat=CSX.AddMaterial("FR4"); mat.SetMaterialProperty(epsilon=EPSR,kappa=2*np.pi*f0*EPS0*EPSR*TAND)
    mat.AddBox(start=[0,0,0],stop=[xs,ys,H_SUB_s])
    gnd=CSX.AddMetal("gnd"); gnd.AddBox(start=[-step*PML,-step*PML,-0.5e-3],stop=[xs+step*PML,ys+step*PML,0])
    if with_patch:
        pm=CSX.AddMetal("patch"); pm.AddBox(start=[px1,py1,H_SUB_s],stop=[px2,py2,z_top])

    FDTD=openEMS(); FDTD.SetCSX(CSX); FDTD.SetBoundaryCond(["PML_8"]*6)
    FDTD.SetEndCriteria(endcrit); FDTD.SetGaussExcite(f0,1.5e9); FDTD.SetMaxTime(simtime)
    ix=int(np.clip(np.searchsorted(xs_arr,feed_x,side="right")-1,0,len(xs_arr)-2))
    iy=int(np.clip(np.searchsorted(ys_arr,feed_y,side="right")-1,0,len(ys_arr)-2))
    iz0=int(np.where(np.isclose(zs_arr,0.0))[0][0]); iz1=int(np.where(np.isclose(zs_arr,H_SUB_s))[0][0])
    port=FDTD.AddLumpedPort(1,50,start=[xs_arr[ix],ys_arr[iy],zs_arr[iz0]],stop=[xs_arr[ix+1],ys_arr[iy+1],zs_arr[iz1]],p_dir="z",excite=1.0)

    d=WORK/dirname
    if d.exists(): shutil.rmtree(d)
    d.mkdir(parents=True,exist_ok=True)
    t0=time.time(); FDTD.Run(str(d),cleanup=True,verbose=False); print(f"    FDTD {time.time()-t0:.0f}s")

    freqs=np.linspace(1.5e9,3.5e9,401)
    S11=np.zeros(401,dtype=complex)
    for i,freq in enumerate(freqs):
        port.CalcPort(str(d),freq)
        uf_i=float(np.atleast_1d(port.uf_inc)[0]); uf_r=float(np.atleast_1d(port.uf_ref)[0])
        S11[i]=uf_r/uf_i if abs(uf_i)>1e-30 else 0
    s11dB=20*np.log10(np.abs(S11)+1e-30)
    i0=np.argmin(np.abs(freqs-f0))
    ires=np.argmin(s11dB)
    print(f"    S11 at f0={f0/1e9:.2f}GHz: {s11dB[i0]:.1f} dB | min S11={s11dB[ires]:.1f} dB at {freqs[ires]/1e9:.2f}GHz")
    print(f"    avg S11 1.5-3.5GHz: {np.mean(s11dB):.1f} dB")
    return freqs, s11dB

print("=== Channel B @2.45GHz WITH patch ===")
f1,s1 = run(2.45e9, True, "diag_b_with", simtime=200000, endcrit=1e-6)
print("=== Channel B @2.45GHz WITHOUT patch ===")
f2,s2 = run(2.45e9, False, "diag_b_without", simtime=200000, endcrit=1e-6)
print(f"\nDIFF at f0: {s1[np.argmin(np.abs(f1-2.45e9))]:.1f} dB (with) vs {s2[np.argmin(np.abs(f2-2.45e9))]:.1f} dB (without)")