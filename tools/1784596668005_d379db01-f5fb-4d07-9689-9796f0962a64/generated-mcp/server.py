#!/usr/bin/env python3
"""部门职责映射工具 MCP Server - 维护事件类型与处理部门之间的映射关系"""

import os
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# 从环境变量读取配置，支持默认占位值
API_ENDPOINT = os.environ.get("DEPT_MAPPING_API_ENDPOINT", "http://localhost:8080/api/dept-mapping")
API_KEY = os.environ.get("DEPT_MAPPING_API_KEY", "default_api_key_placeholder")

# 初始化 MCP Server
server = Server("dept-mapping-tool")

# 内存存储映射关系（生产环境应替换为数据库）
dept_mappings = {}

@server.list_tools()
async def list_tools():
    """列出可用的工具"""
    return [
        Tool(
            name="add_dept_mapping",
            description="添加事件类型与处理部门的映射关系",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "description": "事件类型名称，如'投诉举报'、'咨询建议'等"
                    },
                    "dept_id": {
                        "type": "string",
                        "description": "处理部门 ID"
                    },
                    "dept_name": {
                        "type": "string",
                        "description": "处理部门名称"
                    },
                    "priority": {
                        "type": "integer",
                        "description": "优先级，1-5，数字越大优先级越高",
                        "default": 3
                    }
                },
                "required": ["event_type", "dept_id", "dept_name"]
            }
        ),
        Tool(
            name="query_dept_mapping",
            description="查询事件类型对应的处理部门",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "description": "事件类型名称"
                    }
                },
                "required": ["event_type"]
            }
        ),
        Tool(
            name="list_all_mappings",
            description="列出所有事件类型与部门的映射关系",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        Tool(
            name="delete_dept_mapping",
            description="删除指定的事件类型与部门映射关系",
            inputSchema={
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "description": "要删除的事件类型名称"
                    }
                },
                "required": ["event_type"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    """处理工具调用"""
    
    if name == "add_dept_mapping":
        event_type = arguments.get("event_type")
        dept_id = arguments.get("dept_id")
        dept_name = arguments.get("dept_name")
        priority = arguments.get("priority", 3)
        
        # TODO: 这里应该调用外部 API 持久化存储
        # 当前使用内存存储作为最小可用实现
        dept_mappings[event_type] = {
            "dept_id": dept_id,
            "dept_name": dept_name,
            "priority": priority
        }
        
        return [TextContent(
            type="text",
            text=json.dumps({
                "success": True,
                "message": f"已添加映射：{event_type} -> {dept_name}",
                "data": dept_mappings[event_type]
            }, ensure_ascii=False, indent=2)
        )]
    
    elif name == "query_dept_mapping":
        event_type = arguments.get("event_type")
        
        if event_type in dept_mappings:
            return [TextContent(
                type="text",
                text=json.dumps({
                    "success": True,
                    "data": {
                        "event_type": event_type,
                        **dept_mappings[event_type]
                    }
                }, ensure_ascii=False, indent=2)
            )]
        else:
            return [TextContent(
                type="text",
                text=json.dumps({
                    "success": False,
                    "message": f"未找到事件类型 '{event_type}' 的映射关系",
                    "data": None
                }, ensure_ascii=False, indent=2)
            )]
    
    elif name == "list_all_mappings":
        return [TextContent(
            type="text",
            text=json.dumps({
                "success": True,
                "count": len(dept_mappings),
                "data": dept_mappings
            }, ensure_ascii=False, indent=2)
        )]
    
    elif name == "delete_dept_mapping":
        event_type = arguments.get("event_type")
        
        if event_type in dept_mappings:
            deleted = dept_mappings.pop(event_type)
            return [TextContent(
                type="text",
                text=json.dumps({
                    "success": True,
                    "message": f"已删除映射：{event_type}",
                    "data": deleted
                }, ensure_ascii=False, indent=2)
            )]
        else:
            return [TextContent(
                type="text",
                text=json.dumps({
                    "success": False,
                    "message": f"未找到事件类型 '{event_type}' 的映射关系",
                    "data": None
                }, ensure_ascii=False, indent=2)
            )]
    
    else:
        return [TextContent(
            type="text",
            text=json.dumps({
                "success": False,
                "message": f"未知工具：{name}"
            }, ensure_ascii=False, indent=2)
        )]

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
