from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import URLError
from urllib.request import Request, urlopen

from omni_analyst.models.contracts import SkillDefinition
from omni_analyst.skills.runtime import SkillRuntime


@dataclass
class CreatedSkillPackage:
    skill: SkillDefinition
    root: str
    files: List[str]
    diagnostics: List[str]


class AutonomousSkillCreator:
    """Creates Agent Skills-compatible folders from a user prompt and references.

    The output follows the public Agent Skills shape: a folder with a required
    SKILL.md plus optional scripts and references. Generation is deterministic
    and local-first so skill creation is auditable and testable.
    """

    def __init__(self, skill_runtime: SkillRuntime | None = None) -> None:
        self.skill_runtime = skill_runtime

    def create(
        self,
        *,
        prompt: str,
        name: str | None = None,
        description: str | None = None,
        root: str | Path = ".agents/skills",
        namespace: str = "local",
        reference_urls: List[str] | None = None,
        allowed_tools: List[str] | None = None,
        include_python_runner: bool = True,
        fetch_references: bool = False,
        overwrite: bool = False,
    ) -> CreatedSkillPackage:
        if not prompt.strip():
            raise ValueError("prompt is required to create a skill")
        skill_name = name or self._name_from_prompt(prompt)
        skill_slug = self._slug(skill_name)
        skill_root = Path(root) / skill_slug
        if skill_root.exists() and not overwrite:
            raise FileExistsError(f"Skill already exists: {skill_root}")
        skill_root.mkdir(parents=True, exist_ok=True)
        diagnostics: list[str] = []
        urls = reference_urls or []
        references = self._write_references(
            skill_root,
            urls,
            fetch=fetch_references,
            diagnostics=diagnostics,
        )
        instructions = self._instructions(prompt, urls)
        skill_md = self._skill_markdown(
            name=skill_name,
            description=description or self._description_from_prompt(prompt),
            prompt=prompt,
            instructions=instructions,
            reference_urls=urls,
            allowed_tools=allowed_tools or [],
        )
        files: list[str] = []
        skill_path = skill_root / "SKILL.md"
        skill_path.write_text(skill_md, encoding="utf-8")
        files.append(str(skill_path))
        files.extend(references)
        if include_python_runner:
            script_path = self._write_python_runner(skill_root, prompt, urls)
            files.append(str(script_path))
        manifest_path = skill_root / "skill.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "name": skill_name,
                    "description": description or self._description_from_prompt(prompt),
                    "source": "autonomous_skill_creator",
                    "reference_urls": urls,
                    "files": [Path(path).relative_to(skill_root).as_posix() for path in files],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        files.append(str(manifest_path))
        skill = SkillRuntime().load_file(skill_path, skill_root.parent, namespace)
        if self.skill_runtime is not None:
            skill = self.skill_runtime.register(skill)
        return CreatedSkillPackage(
            skill=skill,
            root=str(skill_root),
            files=files,
            diagnostics=diagnostics,
        )

    def _skill_markdown(
        self,
        *,
        name: str,
        description: str,
        prompt: str,
        instructions: str,
        reference_urls: List[str],
        allowed_tools: List[str],
    ) -> str:
        trigger_terms = ", ".join(self._keywords(prompt)[:8])
        tools = ", ".join(allowed_tools)
        references = "\n".join(f"- {url}" for url in reference_urls) or "- None"
        return textwrap.dedent(
            f"""\
            ---
            name: {name}
            description: {description}
            triggers: [{trigger_terms}]
            allowed_tools: [{tools}]
            resources: [references]
            ---

            # {name}

            ## Purpose
            {description}

            ## When To Use
            Use this skill when the user request matches the intent, terms, or workflow below:

            ```text
            {prompt.strip()}
            ```

            ## Procedure
            {instructions}

            ## Inputs To Collect
            - User goal and expected output format.
            - Relevant files, URLs, logs, screenshots, data, or constraints.
            - Success criteria and verification steps.

            ## Verification
            - Confirm the output directly satisfies the user goal.
            - Cite or preserve provenance for any external reference.
            - Run available checks or provide clear diagnostics when checks are not possible.

            ## References
            {references}
            """
        )

    def _instructions(self, prompt: str, urls: List[str]) -> str:
        steps = [
            "1. Restate the target outcome in operational terms.",
            "2. Inspect local project context before making assumptions.",
            "3. Use the bundled references only as supporting context and preserve citations.",
            "4. Produce the smallest complete implementation or answer that satisfies the goal.",
            "5. Validate the result with tests, static checks, or deterministic reasoning.",
        ]
        if urls:
            steps.insert(2, "3. Review the reference URLs listed in this skill before designing the workflow.")
            steps = [step.replace("3.", "4.").replace("4.", "5.").replace("5.", "6.") if index >= 3 else step for index, step in enumerate(steps)]
        return "\n".join(steps)

    def _write_python_runner(
        self, skill_root: Path, prompt: str, reference_urls: List[str]
    ) -> Path:
        scripts = skill_root / "scripts"
        scripts.mkdir(exist_ok=True)
        script = scripts / "run_skill.py"
        script.write_text(
            textwrap.dedent(
                f'''\
                from __future__ import annotations

                import argparse
                import json

                SKILL_PROMPT = {prompt!r}
                REFERENCE_URLS = {reference_urls!r}


                def build_execution_plan(task: str) -> dict:
                    return {{
                        "skill_prompt": SKILL_PROMPT,
                        "task": task,
                        "reference_urls": REFERENCE_URLS,
                        "steps": [
                            "understand_task",
                            "inspect_context",
                            "apply_skill_instructions",
                            "verify_result",
                        ],
                    }}


                def main() -> None:
                    parser = argparse.ArgumentParser()
                    parser.add_argument("task", nargs="?", default="")
                    args = parser.parse_args()
                    print(json.dumps(build_execution_plan(args.task), indent=2))


                if __name__ == "__main__":
                    main()
                '''
            ),
            encoding="utf-8",
        )
        return script

    def _write_references(
        self,
        skill_root: Path,
        urls: List[str],
        *,
        fetch: bool,
        diagnostics: List[str],
    ) -> List[str]:
        references_dir = skill_root / "references"
        references_dir.mkdir(exist_ok=True)
        index_path = references_dir / "index.md"
        index_path.write_text(
            "# References\n\n" + "\n".join(f"- {url}" for url in urls),
            encoding="utf-8",
        )
        files = [str(index_path)]
        if not fetch:
            return files
        for index, url in enumerate(urls):
            try:
                request = Request(url, headers={"User-Agent": "omniagent-skill-creator"})
                with urlopen(request, timeout=10) as response:
                    content = response.read(200_000).decode("utf-8", errors="ignore")
                path = references_dir / f"reference-{index}.txt"
                path.write_text(content, encoding="utf-8")
                files.append(str(path))
            except (OSError, URLError, TimeoutError) as exc:
                diagnostics.append(f"Could not fetch {url}: {exc}")
        return files

    def _name_from_prompt(self, prompt: str) -> str:
        keywords = self._keywords(prompt)
        return " ".join(word.title() for word in keywords[:4]) or "Generated Skill"

    def _description_from_prompt(self, prompt: str) -> str:
        compact = " ".join(prompt.split())
        return compact[:180] if compact else "Generated autonomous skill"

    def _keywords(self, text: str) -> List[str]:
        stop = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "from",
            "have",
            "create",
            "skill",
            "agent",
            "should",
        }
        words = re.findall(r"[a-z0-9]+", text.lower())
        seen: set[str] = set()
        keywords: list[str] = []
        for word in words:
            if len(word) < 3 or word in stop or word in seen:
                continue
            seen.add(word)
            keywords.append(word)
        return keywords

    def _slug(self, name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return slug or "generated-skill"
