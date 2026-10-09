from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from omni_analyst.models.contracts import SkillDefinition, SkillMatch


class SkillRuntime:
    """Loads and matches markdown skills from plugins and local skill folders."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> SkillDefinition:
        self._skills[skill.skill_id] = skill
        return skill

    def discover(
        self,
        root: str | Path,
        *,
        namespace: str = "default",
    ) -> List[SkillDefinition]:
        root_path = Path(root)
        candidates = [
            root_path / ".agents" / "skills",
            root_path / "skills",
            root_path / "commands",
        ]
        loaded: list[SkillDefinition] = []
        for folder in candidates:
            if not folder.exists():
                continue
            for skill_path in folder.rglob("SKILL.md"):
                loaded.append(self.register(self.load_file(skill_path, root_path, namespace)))
        return loaded

    def load_file(
        self, path: str | Path, root: str | Path | None = None, namespace: str = "default"
    ) -> SkillDefinition:
        skill_path = Path(path)
        root_path = Path(root) if root else skill_path.parent
        content = skill_path.read_text(encoding="utf-8").lstrip()
        metadata, body = self._parse_frontmatter(content)
        name = str(metadata.get("name") or skill_path.parent.name or skill_path.stem)
        description = str(metadata.get("description") or self._first_paragraph(body))
        triggers = self._coerce_list(metadata.get("triggers") or metadata.get("trigger"))
        allowed_tools = self._coerce_list(metadata.get("allowed_tools") or metadata.get("tools"))
        resources = self._coerce_list(metadata.get("resources"))
        return SkillDefinition(
            skill_id=f"{namespace}:{name}",
            name=name,
            description=description,
            path=skill_path.relative_to(root_path).as_posix()
            if skill_path.is_relative_to(root_path)
            else str(skill_path),
            namespace=namespace,
            triggers=triggers,
            allowed_tools=allowed_tools,
            resources=resources,
            content=body.strip(),
            metadata=metadata,
        )

    def list_skills(self, namespace: str | None = None) -> List[SkillDefinition]:
        skills = list(self._skills.values())
        if namespace is not None:
            skills = [skill for skill in skills if skill.namespace == namespace]
        return sorted(skills, key=lambda skill: (skill.namespace, skill.name))

    def match(self, text: str, *, limit: int = 5) -> List[SkillMatch]:
        tokens = set(self._tokens(text))
        matches: list[SkillMatch] = []
        for skill in self._skills.values():
            haystack = " ".join([skill.name, skill.description, " ".join(skill.triggers)])
            skill_tokens = set(self._tokens(haystack))
            overlap = len(tokens & skill_tokens)
            trigger_hits = [
                trigger for trigger in skill.triggers if trigger.lower() in text.lower()
            ]
            score = overlap / max(len(tokens | skill_tokens), 1)
            if trigger_hits:
                score += 0.5
            if score > 0:
                matches.append(
                    SkillMatch(
                        skill=skill,
                        score=round(score, 4),
                        reasons=trigger_hits or ["keyword_overlap"],
                    )
                )
        return sorted(matches, key=lambda match: match.score, reverse=True)[:limit]

    def invocation_context(self, skill_id: str) -> Dict[str, Any]:
        if skill_id not in self._skills:
            raise KeyError(f"Unknown skill: {skill_id}")
        skill = self._skills[skill_id]
        return {
            "skill_id": skill.skill_id,
            "name": skill.name,
            "description": skill.description,
            "instructions": skill.content,
            "allowed_tools": skill.allowed_tools,
            "resources": skill.resources,
            "metadata": skill.metadata,
        }

    def _parse_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        if not content.startswith("---"):
            return {}, content
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content
        metadata: dict[str, Any] = {}
        for line in parts[1].splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            metadata[key.strip()] = self._parse_value(value.strip())
        return metadata, parts[2]

    def _parse_value(self, value: str) -> Any:
        if value.startswith("[") and value.endswith("]"):
            return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
        return value.strip("'\"")

    def _coerce_list(self, value: Any) -> List[str]:
        if value is None or value == "":
            return []
        if isinstance(value, list):
            return [str(item) for item in value]
        return [part.strip() for part in str(value).split(",") if part.strip()]

    def _first_paragraph(self, body: str) -> str:
        for block in body.split("\n\n"):
            text = block.strip().lstrip("#").strip()
            if text:
                return text[:240]
        return ""

    def _tokens(self, text: str) -> List[str]:
        return re.findall(r"[a-z0-9]+", text.lower())
