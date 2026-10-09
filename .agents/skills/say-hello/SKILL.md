            ---
            name: Say Hello
            description: Reusable workflow learned from autonomous run. Original request: Say hello Outcome: system: You are OmniAgent autonomous mode, inspired by OpenClaw, Claude Code, Codex, OpenClaude,
            triggers: [reusable, workflow, learned, autonomous, run, original, request, say]
            allowed_tools: []
            resources: [references]
            ---

            # Say Hello

            ## Purpose
            Reusable workflow learned from autonomous run. Original request: Say hello Outcome: system: You are OmniAgent autonomous mode, inspired by OpenClaw, Claude Code, Codex, OpenClaude,

            ## When To Use
            Use this skill when the user request matches the intent, terms, or workflow below:

            ```text
            Reusable workflow learned from autonomous run.
Original request: Say hello
Outcome: system: You are OmniAgent autonomous mode, inspired by OpenClaw, Claude Code, Codex, OpenClaude, and Hermes Agent. Operate autonomously, use tools safely, obey project instructions, checkpoint progress, verify work, and synthesize reusable skills.

## Project Instructions
None

## Persistent Markdown Memory
None

## Plugin Capability Inventory
None

## User Goal
Say hello

Async subagent rules:
- Use deep:start_async_task for long-running or parallel work and return the full task_id to the user.
- Do not immediately poll after launch unless the user explicitly asks for status.
- Treat task status in conversation history as stale; call deep:check_async_task or deep:list_async_tasks before reporting it.
- Use deep:update_async_task to steer a live task and deep:cancel_async_task to stop it.

Relevant memory:
[{'type': 'user_query', 'query': 'hello', 'metadata': {'middleware': {'query': 'hello', 'session_id': None, 'metadata': {}, 'prompt_injection_risk': 0.0, 'pii_detected': False, 'rate_limit_remaining': 57, 'tool_call_limit': 20, 'selected_skills': [], 'grounding_required': True, 'graph_context': [], 'cache_namespace': 'default', 'cache_enabled': True, 'web_search_allowed': True, 'provenance': {'middleware_attested': True}, 'audit_events': [{'phase': 'unknown', 'session_id': None, 'blocked': False, 'warnings': []}, {'phase': 'unknown', 'session_id': None, 'blocked': False, 'warnings': []}], 'middleware_fired': ['prompt_injection', 'secret_redaction', 'pii', 'rate_limit', 'tool_call_limit', 'tool_permission', 'model_budget', 'skills', 'retrieval_grounding', 'graph_context', 'retrieval_citation', 'output_schema', 'cache_policy', 'web_search_policy', 'provenance', 'audit']}}, 'created_at': '2026-05-05T15:36:15.065500+00:00', 'namespace': 'short_term', 'owner_id': 'session-unknown', 'memory_id': 'mem-3319988f63f04298', 'score': 0.0139}]
user: Say hello
            ```

            ## Procedure
            1. Restate the target outcome in operational terms.
2. Inspect local project context before making assumptions.
3. Review the reference URLs listed in this skill before designing the workflow.
6. Use the bundled references only as supporting context and preserve citations.
6. Produce the smallest complete implementation or answer that satisfies the goal.
6. Validate the result with tests, static checks, or deterministic reasoning.

            ## Inputs To Collect
            - User goal and expected output format.
            - Relevant files, URLs, logs, screenshots, data, or constraints.
            - Success criteria and verification steps.

            ## Verification
            - Confirm the output directly satisfies the user goal.
            - Cite or preserve provenance for any external reference.
            - Run available checks or provide clear diagnostics when checks are not possible.

            ## References
            - https://github.com/openclaw/openclaw
- https://github.com/anthropics/claude-code
- https://github.com/openai/codex
- https://github.com/Gitlawb/openclaude
- https://github.com/nousresearch/hermes-agent
