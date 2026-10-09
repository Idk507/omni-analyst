from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List


class DocumentDNA:
    def fingerprint(
        self, text: str, metadata: Dict[str, Any] | None = None, *, shingle_size: int = 5
    ) -> Dict[str, Any]:
        normalized = self._normalize(text)
        tokens = re.findall(r"[a-z0-9]+", normalized)
        frequencies = Counter(tokens)
        signature = " ".join(token for token, _ in frequencies.most_common(30))
        shingles = self._shingles(tokens, shingle_size)
        simhash = self._simhash(tokens)
        sections = self._sections(text)
        return {
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "normalized_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
            "simhash": simhash,
            "token_count": len(tokens),
            "unique_token_count": len(frequencies),
            "top_terms": frequencies.most_common(20),
            "shingles": sorted(shingles)[:200],
            "shingle_count": len(shingles),
            "sections": sections,
            "signature": signature,
            "metadata": metadata or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def _normalize(self, text: str) -> str:
        return " ".join(re.findall(r"[a-z0-9]+", text.lower()))

    def _shingles(self, tokens: List[str], size: int) -> set[str]:
        if len(tokens) < size:
            return {" ".join(tokens)} if tokens else set()
        return {" ".join(tokens[index : index + size]) for index in range(len(tokens) - size + 1)}

    def _simhash(self, tokens: List[str], bits: int = 64) -> str:
        weights = [0] * bits
        for token, count in Counter(tokens).items():
            digest = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
            for bit in range(bits):
                weights[bit] += count if digest & (1 << bit) else -count
        value = 0
        for bit, weight in enumerate(weights):
            if weight >= 0:
                value |= 1 << bit
        return f"{value:016x}"

    def _sections(self, text: str) -> List[Dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        current_title = "body"
        current_lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or (stripped.endswith(":") and len(stripped) < 120):
                if current_lines:
                    sections.append(self._section(current_title, current_lines))
                current_title = stripped.strip("#: ").lower() or "section"
                current_lines = []
            elif stripped:
                current_lines.append(stripped)
        if current_lines:
            sections.append(self._section(current_title, current_lines))
        return sections or [self._section("body", [text])]

    def _section(self, title: str, lines: List[str]) -> Dict[str, Any]:
        content = "\n".join(lines)
        return {
            "title": title,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "token_count": len(re.findall(r"[a-z0-9]+", content.lower())),
        }


class DriftDetector:
    def compare(
        self,
        previous: Dict[str, Any],
        current: Dict[str, Any],
        *,
        review_threshold: float = 0.35,
    ) -> Dict[str, Any]:
        prev_terms = {term for term, _ in previous.get("top_terms", [])}
        curr_terms = {term for term, _ in current.get("top_terms", [])}
        term_similarity = self._jaccard(prev_terms, curr_terms)
        shingle_similarity = self._jaccard(
            set(previous.get("shingles", [])),
            set(current.get("shingles", [])),
        )
        hash_similarity = self._simhash_similarity(
            str(previous.get("simhash", "")),
            str(current.get("simhash", "")),
        )
        similarity = round(
            (term_similarity * 0.4) + (shingle_similarity * 0.4) + (hash_similarity * 0.2),
            4,
        )
        drift = round(1.0 - similarity, 4)
        return {
            "similarity": similarity,
            "term_similarity": round(term_similarity, 4),
            "shingle_similarity": round(shingle_similarity, 4),
            "simhash_similarity": round(hash_similarity, 4),
            "drift": drift,
            "new_terms": sorted(curr_terms - prev_terms),
            "removed_terms": sorted(prev_terms - curr_terms),
            "requires_review": drift >= review_threshold,
            "threshold": review_threshold,
            "compared_at": datetime.now(timezone.utc).isoformat(),
        }

    def _jaccard(self, left: set[str], right: set[str]) -> float:
        union = left | right
        return len(left & right) / len(union) if union else 1.0

    def _simhash_similarity(self, left: str, right: str) -> float:
        if not left or not right:
            return 0.0
        left_int = int(left, 16)
        right_int = int(right, 16)
        distance = (left_int ^ right_int).bit_count()
        return 1.0 - (distance / 64)


class ConsensusRuntime:
    def decide(
        self,
        votes: List[Dict[str, Any]],
        *,
        quorum: float = 0.67,
        weight_key: str = "confidence",
    ) -> Dict[str, Any]:
        weighted: dict[str, float] = {}
        vote_audit: list[dict[str, Any]] = []
        for index, vote in enumerate(votes):
            answer = str(vote.get("answer", ""))
            weight = float(vote.get(weight_key, 1.0) or 1.0)
            weighted[answer] = weighted.get(answer, 0.0) + weight
            vote_audit.append(
                {
                    "index": index,
                    "agent_id": vote.get("agent_id"),
                    "answer": answer,
                    "weight": weight,
                    "rationale": vote.get("rationale", ""),
                }
            )
        total_weight = sum(weighted.values())
        answer, weight = max(weighted.items(), key=lambda item: item[1]) if weighted else ("", 0.0)
        agreement = weight / total_weight if total_weight else 0.0
        dissent = [
            {"answer": candidate, "weight": candidate_weight}
            for candidate, candidate_weight in sorted(weighted.items())
            if candidate != answer
        ]
        return {
            "answer": answer,
            "votes": votes,
            "vote_audit": vote_audit,
            "weighted_tally": weighted,
            "agreement": round(agreement, 4),
            "quorum": quorum,
            "dissent": dissent,
            "requires_review": bool(votes and agreement < quorum),
            "decided_at": datetime.now(timezone.utc).isoformat(),
        }
