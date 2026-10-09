from __future__ import annotations

import re
from typing import Any, Dict, List


class ExplainabilityPanelBuilder:
    def build(
        self,
        *,
        tasks: List[Dict[str, Any]],
        retrieval: List[Dict[str, Any]] | None = None,
        graph_paths: List[Dict[str, Any]] | None = None,
        middleware: List[str] | None = None,
        hook_records: List[Dict[str, Any]] | None = None,
        cache_records: List[Dict[str, Any]] | None = None,
        confidence_marks: List[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        retrieval_items = retrieval or []
        graph_items = graph_paths or []
        middleware_items = middleware or []
        hooks = hook_records or []
        cache = cache_records or []
        confidence = confidence_marks or []
        provenance = self._provenance(tasks, retrieval_items, graph_items, hooks, cache)
        risk_flags = self._risk_flags(tasks, middleware_items, hooks, confidence)
        return {
            "tasks": tasks,
            "retrieval": retrieval_items,
            "graph_paths": graph_items,
            "middleware": middleware_items,
            "hook_records": hooks,
            "cache_records": cache,
            "confidence": confidence,
            "provenance": provenance,
            "risk_flags": risk_flags,
            "summary": {
                "task_count": len(tasks),
                "retrieval_count": len(retrieval_items),
                "graph_path_count": len(graph_items),
                "middleware_count": len(middleware_items),
                "hook_count": len(hooks),
                "cache_hit_count": sum(1 for item in cache if item.get("cache_hit")),
                "average_confidence": self._average_confidence(confidence),
                "risk_flag_count": len(risk_flags),
            },
        }

    def _provenance(
        self,
        tasks: List[Dict[str, Any]],
        retrieval: List[Dict[str, Any]],
        graph_paths: List[Dict[str, Any]],
        hooks: List[Dict[str, Any]],
        cache: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "task_ids": [str(task.get("task_id")) for task in tasks if task.get("task_id")],
            "evidence_urls": [
                item.get("url") for item in retrieval if item.get("url")
            ],
            "graph_path_ids": [
                item.get("path_id") or item.get("id") for item in graph_paths if item
            ],
            "hook_record_ids": [
                item.get("record_id") for item in hooks if item.get("record_id")
            ],
            "cache_keys": [item.get("key") for item in cache if item.get("key")],
        }

    def _risk_flags(
        self,
        tasks: List[Dict[str, Any]],
        middleware: List[str],
        hooks: List[Dict[str, Any]],
        confidence: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        flags: list[dict[str, Any]] = []
        if any(task.get("status") == "FAILED" for task in tasks):
            flags.append({"type": "task_failure", "severity": "high"})
        if any(not hook.get("allowed", True) for hook in hooks):
            flags.append({"type": "hook_block", "severity": "high"})
        if "pii" in middleware:
            flags.append({"type": "pii_checked", "severity": "info"})
        low_confidence = [item for item in confidence if item.get("band") == "low"]
        if low_confidence:
            flags.append(
                {
                    "type": "low_confidence_claims",
                    "severity": "medium",
                    "count": len(low_confidence),
                }
            )
        return flags

    def _average_confidence(self, confidence: List[Dict[str, Any]]) -> float:
        if not confidence:
            return 0.0
        return round(
            sum(float(item.get("confidence", 0.0)) for item in confidence) / len(confidence),
            4,
        )


class ConfidenceWatermarker:
    def watermark(
        self,
        sentences: List[str],
        evidence_scores: List[float],
        evidence: List[Dict[str, Any]] | None = None,
    ) -> List[Dict[str, Any]]:
        output: list[dict[str, Any]] = []
        evidence_items = evidence or []
        for index, sentence in enumerate(sentences):
            supplied_score = evidence_scores[index] if index < len(evidence_scores) else 0.5
            support = self._support_score(sentence, evidence_items)
            score = (supplied_score * 0.85) + (support * 0.15)
            bounded = max(0.0, min(1.0, score))
            output.append(
                {
                    "sentence": sentence,
                    "confidence": round(bounded, 4),
                    "band": "high" if bounded >= 0.75 else "medium" if bounded >= 0.45 else "low",
                    "evidence_support": round(support, 4),
                    "watermark": self._watermark(index, sentence, bounded),
                    "requires_review": bounded < 0.45,
                }
            )
        return output

    def _support_score(self, sentence: str, evidence: List[Dict[str, Any]]) -> float:
        sentence_tokens = set(re.findall(r"[a-z0-9]+", sentence.lower()))
        if not sentence_tokens or not evidence:
            return 0.5
        best = 0.0
        for item in evidence:
            text = " ".join(
                str(item.get(key, "")) for key in ["title", "snippet", "text", "evidence"]
            )
            evidence_tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
            if not evidence_tokens:
                continue
            best = max(best, len(sentence_tokens & evidence_tokens) / len(sentence_tokens | evidence_tokens))
        return best

    def _watermark(self, index: int, sentence: str, confidence: float) -> str:
        compact = re.sub(r"\s+", " ", sentence.strip()).lower()
        return f"cw-{index}-{abs(hash((compact, round(confidence, 2)))) % 1_000_000:06d}"
