#!/usr/bin/python3
"""Governed repair of machine-generated Knowledge -> existing Project links."""

from __future__ import annotations

import os

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, "/opt/paw-mcp-gateway")
from paw_gpt_brain_policy_committer import commit_policy_write

DB = os.getenv("PAW_WORKSPACE_DB", str(Path(__file__).resolve().parents[2] / "data" / "workspace.db"))
STATE = "/var/lib/paw-mcp-gateway/paw_knowledge_project_repair.json"
SYSTEM_APPROVAL = "SYSTEM_EVIDENCE_GOVERNED_WRITE_V1"


def _conn():
    c=sqlite3.connect(f"file:{DB}?mode=ro",uri=True); c.row_factory=sqlite3.Row; return c


def run() -> dict[str, Any]:
    bundles=json.loads(Path("/var/lib/paw-mcp-gateway/paw_gpt_review_bundles.json").read_text())
    bmap=bundles.get("bundles") or {}
    c=_conn()
    try:
        projects=[dict(r) for r in c.execute("select id,name from project").fetchall()]
        by_id={r["id"]:r for r in projects}; by_name={r["name"]:r for r in projects}
        links={(r["knowledge_id"],r["project_id"]) for r in c.execute("select knowledge_id,project_id from knowledge_project_link")}
        pages={r["id"]:dict(r) for r in c.execute("select id,title,slug from knowledge_page").fetchall()}
    finally: c.close()
    candidates=[]; seen=set(); content_updates=[]
    for item in (bundles.get("policy_writes") or {}).values():
        if item.get("status")!="COMMITTED" or item.get("target_type")!="knowledge" or item.get("operation")!="create": continue
        ref=(item.get("commit_ref") or {}).get("target_id")
        if not ref or ref not in pages: continue
        bundle=bmap.get(item.get("bundle_id")) or {}
        proposal=next((p for p in bundle.get("knowledge_proposals") or [] if p.get("id")==item.get("proposal_id")),None)
        if not proposal: proposal=next((p for p in bundle.get("knowledge_proposals") or [] if p.get("canonical_topic")==item.get("payload",{}).get("title")),None)
        target=str(
            (bundle.get("risk_route") or {}).get("project_id")
            or bundle.get("project_id")
            or (proposal or {}).get("project_or_domain")
            or ""
        )
        project=by_id.get(target) or by_name.get(target)
        refs=item.get("evidence_refs") or []
        if project:
            key=(ref,project["id"])
            if key not in links and key not in seen:
                seen.add(key); candidates.append({"knowledge_id":ref,"project_id":project["id"],"title":pages[ref]["title"],"project_name":project["name"],"evidence_refs":refs,"source_refs":item.get("source_refs") or []})
        # New canonical pages from the old router predate the content payload;
        # add provenance by the same governed Knowledge update path.
        content_updates.append({"knowledge_id":ref,"title":pages[ref]["title"],"evidence_refs":refs,"source_refs":item.get("source_refs") or [],"bundle_id":item.get("bundle_id")})
    results=[]
    for item in candidates:
        refs=item["evidence_refs"] or [{"formal_state_ref":f"knowledge:{item['knowledge_id']}:canonical"}]
        result=commit_policy_write({"id":f"knowledge-project-link:{item['knowledge_id']}:{item['project_id']}","target_type":"project","operation":"update","payload":{"id":item["project_id"],"knowledge_project_link":{"knowledge_id":item["knowledge_id"],"project_id":item["project_id"],"relation_type":"primary"}},"source_refs":item["source_refs"],"evidence_refs":refs,"approval_source":SYSTEM_APPROVAL,"preanalysis_model":"deterministic-knowledge-project-repair-v1","authoritative_model":"deterministic-knowledge-project-repair-v1"},db_path=DB,bundle={"id":"knowledge-project-repair","state":"CLEAR","coverage_state":"CONSOLIDATED","source_refs":item["source_refs"],"evidence_refs":refs},mark_policy=False)
        result.update({"knowledge_id":item["knowledge_id"],"project_id":item["project_id"],"project_name":item["project_name"]}); results.append(result)
    update_results=[]
    # Only fill provenance where the current latest revision is empty.  Do
    # not overwrite existing human/canonical content.
    c=_conn()
    try:
        for item in content_updates:
            row=c.execute("select kr.content_json from knowledge_revision kr where kr.knowledge_id=? order by kr.revision_no desc limit 1",(item["knowledge_id"],)).fetchone()
            try: content=json.loads(row[0]) if row and row[0] else {}
            except Exception: content={}
            if content: continue
            payload={"id":item["knowledge_id"],"content":{"canonical_topic":item["title"],"evidence_refs":item["evidence_refs"],"source_version_ids":[x.get("source_version_id") for x in item["evidence_refs"] if isinstance(x,dict) and x.get("source_version_id")],"current_effective":True},"change_summary":"Attach persisted evidence provenance to canonical Knowledge"}
            refs=item["evidence_refs"] or [{"formal_state_ref":f"knowledge:{item['knowledge_id']}:canonical"}]
            result=commit_policy_write({"id":f"knowledge-provenance:{item['knowledge_id']}","target_type":"knowledge","operation":"update","payload":payload,"source_refs":item["source_refs"],"evidence_refs":refs,"approval_source":SYSTEM_APPROVAL,"preanalysis_model":"deterministic-knowledge-provenance-v1","authoritative_model":"deterministic-knowledge-provenance-v1"},db_path=DB,bundle={"id":"knowledge-provenance-repair","state":"CLEAR","coverage_state":"CONSOLIDATED","source_refs":item["source_refs"],"evidence_refs":refs},mark_policy=False)
            result.update({"knowledge_id":item["knowledge_id"]}); update_results.append(result)
    finally: c.close()
    report={"schema":"paw-knowledge-project-repair/1","eligible_links":len(candidates),"link_results":results,"provenance_updates":update_results,"formal_state_bypass":0}
    Path(STATE).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=="__main__":
    r=run(); print(json.dumps({"eligible_links":r["eligible_links"],"links_committed":sum(x.get("status")=="COMMITTED" for x in r["link_results"]),"provenance_committed":sum(x.get("status")=="COMMITTED" for x in r["provenance_updates"]),"formal_state_bypass":0},ensure_ascii=False,indent=2))
