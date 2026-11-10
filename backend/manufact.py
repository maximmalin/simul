"""Manufacturer profiles, cost estimation, BOM & CPL (from AI-PCB-Generator).

Adapted from ai-pcb-generator/src/pcb/manufacturing.py (MIT).
Works on our circuit.json + netlist — no Board dataclass from their generator.
"""
from __future__ import annotations
import csv, json, math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

@dataclass
class ManufacturerProfile:
    name: str; website: str; min_trace_mm: float = 0.15; min_drill_mm: float = 0.2
    min_clearance_mm: float = 0.15; max_layers: int = 16; max_board_mm: tuple = (400.0,500.0)
    base_price_usd: float = 2.0; price_per_sqcm: float = 0.02; supports_smt: bool = True
    lead_time_days: int = 7
    def to_dict(self): return asdict(self)

MANUFACTURERS = {
 "jlcpcb": ManufacturerProfile("JLCPCB","https://www.jlcpcb.com",0.127,0.2,0.127,20,(400,500),2.0,0.015,True,5),
 "pcbway": ManufacturerProfile("PCBWay","https://www.pcbway.com",0.1,0.2,0.1,14,(500,600),5.0,0.02,True,7),
 "oshpark": ManufacturerProfile("OSH Park","https://oshpark.com",0.152,0.254,0.152,4,(381,432),0.0,0.17,False,12),
}

@dataclass
class CostEstimate:
    manufacturer: str; board_area_sqcm: float; pcb_cost_usd: float; smt_cost_usd: float
    total_cost_usd: float; quantity: int; lead_time_days: int; flags: list = field(default_factory=list)
    def to_dict(self): return asdict(self)

def estimate_cost(circuit, manufacturer_key="jlcpcb", quantity=5) -> CostEstimate:
    prof = MANUFACTURERS.get(manufacturer_key)
    if not prof:
        return CostEstimate(manufacturer_key,0,0,0,0,quantity,0,["unknown manufacturer"])
    board_el = next((e for e in circuit if e.get("type")=="pcb_board"), {})
    w=float(board_el.get("width",88.6)); h=float(board_el.get("height",85.08)); layers=int(board_el.get("num_layers",4))
    area=w*h/100.0
    pcb_unit=max(prof.base_price_usd, area*prof.price_per_sqcm); pcb_cost=pcb_unit*quantity
    flags=[]
    if layers>prof.max_layers: flags.append(f"{layers} layers > max {prof.max_layers}")
    if w>prof.max_board_mm[0] or h>prof.max_board_mm[1]: flags.append("board exceeds max dimensions")
    # min trace
    min_w=min((r.get("width",1) for e in circuit if e.get("type")=="pcb_trace" for r in (e.get("route") or []) if r.get("width")), default=1)
    if min_w < prof.min_trace_mm: flags.append(f"min trace {min_w}mm < fab min {prof.min_trace_mm}mm")
    smt=0.0
    if prof.supports_smt:
        smd_pads=sum(1 for e in circuit if e.get("type")=="pcb_smtpad")
        comps=sum(1 for e in circuit if e.get("type")=="pcb_component")
        smt = (8.0 if smd_pads else 0) + smd_pads*0.03*quantity + comps*1.5
        if smd_pads: flags.append(f"{smd_pads} SMD pads, {comps} components")
    return CostEstimate(prof.name, round(area,2), round(pcb_cost,2), round(smt,2), round(pcb_cost+smt,2), quantity, prof.lead_time_days, flags)

def _get_ref_from_source(circuit, source_id):
    for e in circuit:
        if e.get("type") == "source_component" and e.get("source_component_id") == source_id:
            return e.get("name") or source_id
    return source_id

def bom_rows(circuit):
    rows=[]; groups={}
    # build map source_component_id -> source_component
    src_map = {e["source_component_id"]: e for e in circuit if e.get("type") == "source_component"}
    for e in circuit:
        if e.get("type")!="source_component": continue
        ref=e.get("name","?"); mpn=e.get("manufacturer_part_number","")
        groups.setdefault((mpn or "?", ref), []).append(ref)
    # better grouping by MPN+footprint
    from collections import defaultdict
    g2 = defaultdict(list)
    for e in circuit:
        if e.get("type")!="source_component": continue
        ref=e.get("name","?")
        mpn=e.get("manufacturer_part_number","")
        g2[(mpn or "DNP", e.get("ftype") or "")].append(ref)
    for (mpn, ftype), refs in g2.items():
        rows.append({"Comment":mpn if mpn != "DNP" else ftype, "Designator":", ".join(sorted(refs)), "Footprint":ftype or "", "LCSC Part#":"", "MPN":mpn if mpn != "DNP" else "", "Quantity":len(refs)})
    return rows

def generate_bom_csv(circuit, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    rows=bom_rows(circuit)
    with open(path,"w",newline="",encoding="utf-8-sig") as f:  # BOM with BOM header for some tools
        w=csv.writer(f)
        w.writerow(["Comment","Designator","Footprint","LCSC Part #","Quantity"])
        for r in rows:
            w.writerow([r["Comment"], r["Designator"], r["Footprint"], r["LCSC Part#"], r["Quantity"]])
    return path

def generate_cpl_csv(circuit, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    # map pcb_component_id to source ref
    src_map = {}
    for e in circuit:
        if e.get("type") == "source_component":
            src_map[e["source_component_id"]] = e.get("name") or e["source_component_id"]
    with open(path,"w",newline="",encoding="utf-8-sig") as f:
        w=csv.writer(f); w.writerow(["Designator","Mid X","Mid Y","Layer","Rotation"])
        for e in circuit:
            if e.get("type")!="pcb_component": continue
            ref = src_map.get(e.get("source_component_id"), e.get("source_component_id",""))
            w.writerow([ref, f"{e['center']['x']:.4f}", f"{e['center']['y']:.4f}", e.get("layer","top"), e.get("rotation",0)])
    return path

def generate_production_package(circuit, output_dir="out/production",
                               manufacturer_key="jlcpcb", gerber_zip=None) -> dict:
    """Production package: BOM + CPL + cost + gerber ZIP (upstream
    generate_production_package adapted — our gerbers come from tscircuit
    `make pcb`, not a Python Board). Returns dict of generated paths."""
    import zipfile, shutil
    out = Path(output_dir); out.mkdir(parents=True, exist_ok=True)
    result: dict[str, str] = {}
    # 1. BOM (JLCPCB-compatible columns, extended with MPN/Description)
    bom_path = out / "BOM.csv"
    Path(bom_path).parent.mkdir(parents=True, exist_ok=True)
    rows = bom_rows(circuit)
    with open(bom_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #",
                    "Manufacturer", "MPN", "Description", "Quantity"])
        for r in rows:
            w.writerow([r["Comment"], r["Designator"], r["Footprint"], r["LCSC Part#"],
                        "", r.get("MPN", ""), "", r["Quantity"]])
    result["bom"] = str(bom_path)
    # 2. CPL (pick-and-place)
    cpl_path = out / "CPL.csv"
    generate_cpl_csv(circuit, cpl_path)
    result["cpl"] = str(cpl_path)
    # 3. Cost estimate for this manufacturer
    est = estimate_cost(circuit, manufacturer_key, quantity=5)
    cost_path = out / "cost.json"
    cost_path.write_text(json.dumps(est.to_dict(), indent=2))
    result["cost"] = str(cost_path)
    # 4. Gerber ZIP (from tscircuit export; re-pack under fab-friendly name)
    src_zip = Path(gerber_zip) if gerber_zip else Path("out/gerbers.zip")
    if src_zip.exists():
        prof = MANUFACTURERS.get(manufacturer_key)
        zip_name = f"{manufacturer_key}_gerbers.zip"
        dst = out / zip_name
        shutil.copy(src_zip, dst)
        result["gerber_zip"] = str(dst)
        result["gerber_zip_note"] = f"copied from {src_zip}; rename layers per {prof.name if prof else manufacturer_key} spec before upload"
    else:
        result["gerber_zip_note"] = f"missing {src_zip} — run `make pcb` first"
    # 5. Manifest
    prof = MANUFACTURERS.get(manufacturer_key)
    manifest = {"manufacturer": prof.name if prof else manufacturer_key,
                "files": {k: v for k, v in result.items() if k != "gerber_zip_note"},
                "note": result.get("gerber_zip_note", "")}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    result["manifest"] = str(out / "manifest.json")
    return result


def main() -> int:
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument("--circuit",default="out/circuit.json"); ap.add_argument("--qty",type=int,default=5); ap.add_argument("--out",default="out/cost.json")
    ap.add_argument("--package", action="store_true", help="generate production package (BOM+CPL+cost+gerber zip)")
    ap.add_argument("--package-dir", default="out/production")
    ap.add_argument("--manufacturer", default="jlcpcb", choices=sorted(MANUFACTURERS))
    a=ap.parse_args()
    try:
        from utils import load_circuit_json
        d = load_circuit_json(a.circuit)
    except Exception:
        d=json.loads(Path(a.circuit).read_text()); 
        if isinstance(d, dict): d=d.get("elements",[d])
    estimates={k: estimate_cost(d,k,a.qty).to_dict() for k in MANUFACTURERS}
    report={"quantity":a.qty,"estimates":estimates}
    if a.package:
        report["production_package"]=generate_production_package(d, a.package_dir, a.manufacturer)
    try:
        from utils import save_json
        save_json(a.out, report)
    except Exception:
        Path(a.out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
