#!/usr/bin/python3
"""Persist historical medium-risk Terra work without changing canonical state."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Mapping
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paw_bootstrap_risk_router import classify_bundle, classify_finding
from paw_gpt_review_bundle import ReviewBundleStore
from paw_review_display_normalization import normalize_terra_item

DEFAULT_BRAIN=Path("/var/lib/paw-mcp-gateway/gpt_brain_bootstrap.json")
DEFAULT_OUT=Path("/var/lib/paw-mcp-gateway/paw_bootstrap_terra_queue.json")

def build_queue(*, brain_path: Path=DEFAULT_BRAIN, out_path: Path=DEFAULT_OUT) -> dict[str,Any]:
    brain=json.loads(brain_path.read_text(encoding="utf-8")); data=ReviewBundleStore().load()
    prior={}
    if out_path.exists():
        try: prior={x.get("id"):x for x in (json.loads(out_path.read_text(encoding="utf-8")).get("items") or []) if isinstance(x,dict)}
        except Exception: prior={}
    items=[]
    for bundle in (data.get("bundles") or {}).values():
        if not isinstance(bundle,Mapping) or bundle.get("state")!="REVIEW_PENDING": continue
        d=classify_bundle(bundle,brain,source_state=((brain.get("bootstrap") or {}).get("source_states") or {}).get(bundle.get("source_version_id")) or {})
        if d.get("route")!="TERRA_REVIEW_REQUIRED": continue
        item={"id":"bootstrap-bundle:"+str(bundle.get("id")),"kind":"bundle","bundle_id":bundle.get("id"),"source_version_ids":d.get("source_version_ids") or [],"project_id":d.get("project_id"),"canonical_topic":d.get("canonical_topic"),"reason_codes":d.get("reason_codes") or [],"evidence_count":d.get("evidence_count",0),"status":"PENDING"}
        display=normalize_terra_item(item)
        item["reason_code_explanations"]=[f"机器原因代码 {code}：{display['machine_state_explanation']}" for code in item.get("reason_codes") or []]
        item["review_display"]={k:v for k,v in display.items() if k!="internal_evidence"}
        item.update({k:prior.get(item["id"],{}).get(k) for k in ("status","claimed_by","submitted_at") if prior.get(item["id"],{}).get(k)})
        items.append(item)
    for finding in (brain.get("findings") or {}).values():
        if not isinstance(finding,Mapping) or finding.get("state") in {"FINALIZED","WRITE_READY","WRITTEN"}: continue
        d=classify_finding(finding,brain)
        if d.get("route")!="TERRA_REVIEW_REQUIRED": continue
        item={"id":"bootstrap-finding:"+str(finding.get("id")),"kind":"finding","finding_id":finding.get("id"),"source_version_ids":list(finding.get("source_version_ids") or []),"project_id":finding.get("project_or_domain"),"canonical_topic":finding.get("proposed_target"),"reason_codes":d.get("reason_codes") or [],"status":"PENDING"}
        display=normalize_terra_item(item)
        item["reason_code_explanations"]=[f"机器原因代码 {code}：{display['machine_state_explanation']}" for code in item.get("reason_codes") or []]
        item["review_display"]={k:v for k,v in display.items() if k!="internal_evidence"}
        item.update({k:prior.get(item["id"],{}).get(k) for k in ("status","claimed_by","submitted_at") if prior.get(item["id"],{}).get(k)})
        items.append(item)
    result={"schema":"paw-bootstrap-terra-queue/1","items":items,"count":len(items),"pending_count":sum(x.get("status")=="PENDING" for x in items),"formal_state_bypass":0,"full_corpus_reread":False}
    tmp=out_path.with_suffix(out_path.suffix+".tmp"); tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8"); tmp.replace(out_path)
    return result

if __name__=="__main__":
    r=build_queue(); print(json.dumps({"count":r["count"],"pending_count":r["pending_count"],"formal_state_bypass":0},ensure_ascii=False,indent=2))
