from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MCPToolDescriptor(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, Any] = Field(default_factory=dict)


class MCPServerConfig(BaseModel):
    name: str
    url: str
    transport: str = "streamable_http"
    tools: List[MCPToolDescriptor] = Field(default_factory=list)
    interceptor_names: List[str] = Field(default_factory=list)
    timeout_seconds: float = 30.0
    headers: Dict[str, str] = Field(default_factory=dict)
    auth_token_env: Optional[str] = None


class MCPInvocationContext(BaseModel):
    server_name: str
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    headers: Dict[str, str] = Field(default_factory=dict)
    request_context: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    trace_id: Optional[str] = None


class MCPToolCallResult(BaseModel):
    server_name: str
    tool_name: str
    status_code: int
    result: Dict[str, Any] = Field(default_factory=dict)
    chunks: List[Dict[str, Any]] = Field(default_factory=list)


class ResolvedMCPTool(BaseModel):
    qualified_name: str
    server_name: str
    tool_name: str
    description: str
    input_schema: Dict[str, Any] = Field(default_factory=dict)

