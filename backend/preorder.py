"""Pre-order review (text-to-cad/pcb review-*.md structure).

Scope -> Findings (errors first) -> Coverage. Each row: Item | Evidence |
Rule | Result | Fix. Result in {pass, fail, review, unverified}.
"""
from __future__ import annotations
import json
from pathlib import Path

def build_review(dfm_path="out/dfm.json", cost_path="out/cost.json", erc_path="out/erc_zones.json", score_path="out/pcb_score.json") -> dict:
    dfm=json.loads(Path(dfm_path).read_text()) if Path(dfm_path).exists() else {"score":None,"issues":[]}
    cost=json.loads(Path(cost_path).read_text()) if Path(cost_path).exists() else {"estimates":{}}
    erc=json.loads(Path(erc_path).read_text()) if Path(erc_path).exists() else {"issues":[]}
    score=json.loads(Path(score_path).read_text()) if Path(score_path).exists() else {}
    rows=[]
    # Fabrication
    rows.append({"area":"Fabrication","item":"DFM score","evidence":f"dfm.json score={dfm.get('score')}, issues={dfm.get('issue_count')}","rule":"JLCPCB standard (0.127mm trace/min)","result":"pass" if (dfm.get('score') or 0) >= 80 else "review" if dfm.get('score') is not None else "unverified","fix":"See DFM findings."})
    # Electrical
    rows.append({"area":"Electrical","item":"ERC zones","evidence":f"erc_zones.json issues={erc.get('issue_count')}","rule":"ERC001..ERC008, alias-matched","result":"pass" if erc.get('issue_count')==0 else "review","fix":"Add missing decoupling/ground."})
    # Assembly
    jlc=cost.get("estimates",{}).get("jlcpcb",{})
    rows.append({"area":"Assembly","item":"Cost/SMT","evidence":f"JLCPCB total={jlc.get('total_cost_usd')}, flags={jlc.get('flags')}","rule":"JLCPCB capability page","result":"pass" if jlc.get('total_cost_usd') else "unverified","fix":"Check BOM extended parts."})
    return {"scope":{"board":"out/circuit.json","fab":"JLCPCB (standard)","result":"draft — review, NOT manufacturability approval"},"findings":rows,"notes":"No findings does NOT mean approved to order. Fix only when asked, then re-check neighbours."}

def main() -> int:
    import argparse
    ap=argparse.ArgumentParser(); ap.add_argument("--out", default="out/preorder.json")
    ap.add_argument("--dfm", default="out/dfm.json"); ap.add_argument("--cost", default="out/cost.json"); ap.add_argument("--erc", default="out/erc_zones.json")
    a=ap.parse_args()
    r=build_review(a.dfm, a.cost, a.erc)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True); Path(a.out).write_text(json.dumps(r, indent=2))
    print(json.dumps(r, indent=2))
    return 0

if __name__=="__main__": raise SystemExit(main())
