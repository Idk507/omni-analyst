from __future__ import annotations

from typing import Any, Dict, List


PRESETS: List[Dict[str, Any]] = [
    {
        "id": "github",
        "name": "GitHub",
        "category": "source-control",
        "description": "Connect a GitHub repo or organization through MCP for issues, PRs, and code search.",
        "default_url": "https://api.githubcopilot.com/mcp",
        "auth": {"type": "bearer", "env_var": "GITHUB_MCP_TOKEN"},
        "tools": [
            {"name": "github_search_issues", "description": "Search issues in connected GitHub repos."},
            {"name": "github_create_issue", "description": "Create a GitHub issue in a connected repo."},
            {"name": "github_open_pr", "description": "Open or update a pull request via the GitHub MCP."},
        ],
    },
    {
        "id": "gitlab",
        "name": "GitLab",
        "category": "source-control",
        "description": "Connect GitLab projects via MCP for issues, merge requests, and pipelines.",
        "default_url": "https://gitlab.com/api/v4/mcp",
        "auth": {"type": "bearer", "env_var": "GITLAB_MCP_TOKEN"},
        "tools": [
            {"name": "gitlab_list_issues", "description": "List GitLab issues in the connected projects."},
            {"name": "gitlab_create_mr", "description": "Open a GitLab merge request."},
        ],
    },
    {
        "id": "linear",
        "name": "Linear",
        "category": "tickets",
        "description": "Read and create Linear issues from inside OmniAgent.",
        "default_url": "https://mcp.linear.app/sse",
        "auth": {"type": "bearer", "env_var": "LINEAR_MCP_TOKEN"},
        "tools": [
            {"name": "linear_list_issues", "description": "List Linear issues from connected teams."},
            {"name": "linear_create_issue", "description": "Create a Linear issue."},
        ],
    },
    {
        "id": "slack",
        "name": "Slack",
        "category": "communication",
        "description": "Post messages and read channels through a Slack MCP server.",
        "default_url": "https://mcp.slack.com/v1",
        "auth": {"type": "bearer", "env_var": "SLACK_MCP_TOKEN"},
        "tools": [
            {"name": "slack_post_message", "description": "Post a message to a Slack channel."},
            {"name": "slack_list_channels", "description": "List Slack channels available to the connector."},
        ],
    },
    {
        "id": "notion",
        "name": "Notion",
        "category": "knowledge",
        "description": "Search and update Notion pages.",
        "default_url": "https://mcp.notion.com/v1",
        "auth": {"type": "bearer", "env_var": "NOTION_MCP_TOKEN"},
        "tools": [
            {"name": "notion_search", "description": "Search Notion content."},
            {"name": "notion_create_page", "description": "Create a Notion page."},
        ],
    },
    {
        "id": "filesystem",
        "name": "Filesystem (local)",
        "category": "infrastructure",
        "description": "Allow OmniAgent to read approved local folders through an MCP filesystem server.",
        "default_url": "stdio://mcp-filesystem",
        "auth": {"type": "none", "env_var": None},
        "tools": [
            {"name": "fs_list", "description": "List files in an approved directory."},
            {"name": "fs_read", "description": "Read a file from an approved directory."},
        ],
    },
]


def list_presets() -> List[Dict[str, Any]]:
    return [dict(preset) for preset in PRESETS]


def get_preset(preset_id: str) -> Dict[str, Any]:
    for preset in PRESETS:
        if preset["id"] == preset_id:
            return dict(preset)
    raise KeyError(f"Unknown connector preset: {preset_id}")
