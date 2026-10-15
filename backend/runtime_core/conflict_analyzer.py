"""PAW Conflict Analyzer - Compares new evidence against existing formal knowledge.

Identifies:
- Direct updates to existing knowledge concepts
- Divergent facts / metric contradictions
- Project status or deadline discrepancies
- Proposes conflict resolution candidates if human intervention is required
"""
from __future__ import annotations

import difflib
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("paw.conflict")


class ConflictAnalyzer:
    def analyze_knowledge_conflict(
        self,
        new_title: str,
        new_content: str,
        existing_knowledge: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Check if incoming knowledge conflicts with or updates an existing knowledge page."""
        new_title_clean = new_title.replace("【知识】", "").strip()

        best_match = None
        best_ratio = 0.0

        for k in existing_knowledge:
            k_title = (k.get("title") or "").replace("【知识】", "").strip()
            ratio = difflib.SequenceMatcher(None, new_title_clean.lower(), k_title.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_match = k

        # If similarity is significant (>0.6) but content differs
        if best_match and best_ratio > 0.6:
            existing_summary = best_match.get("one_line_summary") or best_match.get("summary") or ""
            # Check content difference
            content_ratio = difflib.SequenceMatcher(
                None, new_content[:500].lower(), (best_match.get("content") or "")[:500].lower()
            ).ratio()

            if content_ratio < 0.85:
                # Potential conflict or version update
                return {
                    "is_conflict": True,
                    "target_id": best_match.get("id"),
                    "existing_title": best_match.get("title"),
                    "existing_summary": existing_summary,
                    "similarity": round(best_ratio, 2),
                    "nature": "UPDATE_SUPERSEDE" if best_ratio > 0.8 else "TOPIC_OVERLAP_DIVERGENCE",
                    "conflict_description": (
                        f"新文档内容与已有知识《{best_match.get('title')}》主题相似度为 {int(best_ratio*100)}%，"
                        f"检测到内容更新或潜在结论分歧。"
                    ),
                    "recommendation": (
                        f"建议将现有知识修订为新版本 (Revision)，或在审核时确认是否替代旧事实。"
                    ),
                }

        return None

    def analyze_project_alignment(
        self,
        extracted_text: str,
        existing_projects: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Check which projects are mentioned in the incoming document."""
        aligned_projects = []
        for p in existing_projects:
            p_name = p.get("name") or ""
            if len(p_name) >= 3 and p_name.lower() in extracted_text.lower():
                aligned_projects.append({
                    "project_id": p.get("id"),
                    "project_name": p_name,
                    "current_status": p.get("status"),
                })
        return aligned_projects


conflict_analyzer = ConflictAnalyzer()
