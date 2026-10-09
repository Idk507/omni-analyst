from __future__ import annotations

import argparse
import json

SKILL_PROMPT = "Reusable workflow learned from autonomous run.\nOriginal request: Say hello\nOutcome: system: You are OmniAgent autonomous mode, inspired by OpenClaw, Claude Code, Codex, OpenClaude, and Hermes Agent. Operate autonomously, use tools safely, obey project instructions, checkpoint progress, verify work, and synthesize reusable skills.\n\n## Project Instructions\nNone\n\n## Persistent Markdown Memory\nNone\n\n## Plugin Capability Inventory\nNone\n\n## User Goal\nSay hello\n\nAsync subagent rules:\n- Use deep:start_async_task for long-running or parallel work and return the full task_id to the user.\n- Do not immediately poll after launch unless the user explicitly asks for status.\n- Treat task status in conversation history as stale; call deep:check_async_task or deep:list_async_tasks before reporting it.\n- Use deep:update_async_task to steer a live task and deep:cancel_async_task to stop it.\n\nRelevant memory:\n[{'type': 'user_query', 'query': 'hello', 'metadata': {'middleware': {'query': 'hello', 'session_id': None, 'metadata': {}, 'prompt_injection_risk': 0.0, 'pii_detected': False, 'rate_limit_remaining': 57, 'tool_call_limit': 20, 'selected_skills': [], 'grounding_required': True, 'graph_context': [], 'cache_namespace': 'default', 'cache_enabled': True, 'web_search_allowed': True, 'provenance': {'middleware_attested': True}, 'audit_events': [{'phase': 'unknown', 'session_id': None, 'blocked': False, 'warnings': []}, {'phase': 'unknown', 'session_id': None, 'blocked': False, 'warnings': []}], 'middleware_fired': ['prompt_injection', 'secret_redaction', 'pii', 'rate_limit', 'tool_call_limit', 'tool_permission', 'model_budget', 'skills', 'retrieval_grounding', 'graph_context', 'retrieval_citation', 'output_schema', 'cache_policy', 'web_search_policy', 'provenance', 'audit']}}, 'created_at': '2026-05-05T15:36:15.065500+00:00', 'namespace': 'short_term', 'owner_id': 'session-unknown', 'memory_id': 'mem-3319988f63f04298', 'score': 0.0139}]\nuser: Say hello"
REFERENCE_URLS = ['https://github.com/openclaw/openclaw', 'https://github.com/anthropics/claude-code', 'https://github.com/openai/codex', 'https://github.com/Gitlawb/openclaude', 'https://github.com/nousresearch/hermes-agent']


def build_execution_plan(task: str) -> dict:
    return {
        "skill_prompt": SKILL_PROMPT,
        "task": task,
        "reference_urls": REFERENCE_URLS,
        "steps": [
            "understand_task",
            "inspect_context",
            "apply_skill_instructions",
            "verify_result",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", nargs="?", default="")
    args = parser.parse_args()
    print(json.dumps(build_execution_plan(args.task), indent=2))


if __name__ == "__main__":
    main()
