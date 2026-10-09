from __future__ import annotations

import json
from typing import AsyncIterator, Dict, List

import httpx

from omni_analyst.mcp.interceptors.registry import MCPInterceptorRegistry
from omni_analyst.mcp.models import (
    MCPInvocationContext,
    MCPToolCallResult,
    ResolvedMCPTool,
)
from omni_analyst.mcp.registry import MCPRegistry


class MCPStreamableHttpClient:
    """Real MCP client that invokes remote tools over streamable HTTP."""

    def __init__(
        self,
        registry: MCPRegistry,
        interceptor_registry: MCPInterceptorRegistry,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.registry = registry
        self.interceptor_registry = interceptor_registry
        self.transport = transport

    def get_tools(self) -> List[ResolvedMCPTool]:
        return self.registry.get_tools()

    async def call_qualified_tool(
        self,
        qualified_name: str,
        *,
        arguments: Dict[str, object] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        request_context: Dict[str, object] | None = None,
    ) -> MCPToolCallResult:
        tool = self.registry.resolve_tool(qualified_name)
        return await self.call_tool(
            server_name=tool.server_name,
            tool_name=tool.tool_name,
            arguments=arguments,
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
            request_context=request_context,
        )

    async def call_tool(
        self,
        *,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, object] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        request_context: Dict[str, object] | None = None,
    ) -> MCPToolCallResult:
        server = self.registry.get_server(server_name)
        interceptors = self.interceptor_registry.resolve_many(server.interceptor_names)
        context = MCPInvocationContext(
            server_name=server_name,
            tool_name=tool_name,
            arguments=dict(arguments or {}),
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
            request_context=dict(request_context or {}),
        )
        for interceptor in interceptors:
            context = interceptor.before_request(context, server)

        payload = {
            "tool_name": context.tool_name,
            "arguments": context.arguments,
            "context": context.request_context,
        }

        async with httpx.AsyncClient(
            timeout=server.timeout_seconds,
            transport=self.transport,
        ) as client:
            async with client.stream(
                "POST",
                server.url,
                headers=context.headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                chunks, result = await self._consume_stream(response)

        for interceptor in reversed(interceptors):
            result = interceptor.after_response(
                context=context,
                server=server,
                response_payload=result,
                status_code=response.status_code,
            )

        return MCPToolCallResult(
            server_name=server_name,
            tool_name=tool_name,
            status_code=response.status_code,
            result=result,
            chunks=chunks,
        )

    async def stream_tool_chunks(
        self,
        *,
        server_name: str,
        tool_name: str,
        arguments: Dict[str, object] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        request_context: Dict[str, object] | None = None,
    ) -> AsyncIterator[dict]:
        server = self.registry.get_server(server_name)
        interceptors = self.interceptor_registry.resolve_many(server.interceptor_names)
        context = MCPInvocationContext(
            server_name=server_name,
            tool_name=tool_name,
            arguments=dict(arguments or {}),
            session_id=session_id,
            user_id=user_id,
            trace_id=trace_id,
            request_context=dict(request_context or {}),
        )
        for interceptor in interceptors:
            context = interceptor.before_request(context, server)

        payload = {
            "tool_name": context.tool_name,
            "arguments": context.arguments,
            "context": context.request_context,
        }

        async with httpx.AsyncClient(
            timeout=server.timeout_seconds,
            transport=self.transport,
        ) as client:
            async with client.stream(
                "POST",
                server.url,
                headers=context.headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    yield self._parse_line(line)

    async def _consume_stream(
        self, response: httpx.Response
    ) -> tuple[List[dict], Dict[str, object]]:
        chunks: List[dict] = []
        async for line in response.aiter_lines():
            if not line:
                continue
            chunks.append(self._parse_line(line))

        if not chunks:
            body = await response.aread()
            if not body:
                return [], {}
            try:
                parsed = json.loads(body.decode("utf-8"))
                if isinstance(parsed, dict):
                    return [parsed], parsed.get("result", parsed)
                return [{"value": parsed}], {"value": parsed}
            except json.JSONDecodeError:
                text = body.decode("utf-8")
                return [{"text": text}], {"text": text}

        if len(chunks) == 1 and "result" in chunks[0]:
            result = chunks[0]["result"]
            return chunks, result if isinstance(result, dict) else {"value": result}

        return chunks, {"chunks": chunks}

    def _parse_line(self, line: str) -> dict:
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
        except json.JSONDecodeError:
            return {"text": line}

