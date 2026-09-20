"""
元数据管理服务 MCP Server
=========================
统一管理数据资产元数据：表结构、字段定义、数据类型、转换规则、血缘关系链路。
提供元数据查询和血缘追溯接口，是字段血缘分析的基础数据层。

传输方式：stdio
外部 API 地址与鉴权通过环境变量注入。
"""

import os
import json
import asyncio
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP

# ── 配置 ──────────────────────────────────────────────
METADATA_API_BASE_URL = os.environ.get(
    "METADATA_API_BASE_URL", "http://localhost:8080/api/v1"
)
METADATA_API_TOKEN = os.environ.get("METADATA_API_TOKEN", "")
REQUEST_TIMEOUT = float(os.environ.get("METADATA_REQUEST_TIMEOUT", "30"))

# ── HTTP 辅助 ─────────────────────────────────────────


def _headers() -> Dict[str, str]:
    """构造请求头，携带鉴权 Token。"""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if METADATA_API_TOKEN:
        headers["Authorization"] = f"Bearer {METADATA_API_TOKEN}"
    return headers


async def _request(method: str, path: str, **kwargs) -> Dict[str, Any]:
    """向元数据管理后端发起 HTTP 请求并返回 JSON。"""
    url = f"{METADATA_API_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.request(
                method, url, headers=_headers(), **kwargs
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        return {
            "success": False,
            "error": f"HTTP {exc.response.status_code}",
            "detail": exc.response.text,
        }
    except httpx.RequestError as exc:
        return {
            "success": False,
            "error": "RequestError",
            "detail": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "success": False,
            "error": "UnexpectedError",
            "detail": str(exc),
        }


# ── MCP Server ────────────────────────────────────────
mcp = FastMCP("metadata-management-service")


@mcp.tool()
async def query_table_metadata(
    table_name: str = "",
    database: str = "",
    schema_name: str = "",
    include_columns: bool = True,
) -> Dict[str, Any]:
    """查询表的元数据信息，包括表结构、列定义、存储属性等。

    Args:
        table_name: 表名，支持模糊匹配；为空时返回全部表
        database: 数据库名称
        schema_name: Schema 名称
        include_columns: 是否在结果中包含列详情，默认 True

    Returns:
        包含表元数据的字典，结构示例::
            {
              "success": true,
              "data": [
                {
                  "table_id": "tbl_001",
                  "database": "default_db",
                  "schema": "public",
                  "table_name": "orders",
                  "table_type": "MANAGED_TABLE",
                  "owner": "data_team",
                  "create_time": "2025-01-01T00:00:00Z",
                  "columns": [ {"name": "order_id", "type": "BIGINT", "comment": "订单ID"} ]
                }
              ],
              "total": 1
            }
    """
    params: Dict[str, Any] = {
        "include_columns": include_columns,
    }
    if table_name:
        params["table_name"] = table_name
    if database:
        params["database"] = database
    if schema_name:
        params["schema"] = schema_name
    # TODO: 根据真实后端接口调整 query params 命名
    return await _request("GET", "/metadata/tables", params=params)


@mcp.tool()
async def query_field_metadata(
    table_name: str,
    column_name: str = "",
    data_type: str = "",
) -> Dict[str, Any]:
    """查询指定表的字段/列元数据，包括数据类型、注释、是否主键、默认值等。

    Args:
        table_name: 目标表名（必填）
        column_name: 列名，支持模糊匹配；为空时返回该表全部列
        data_type: 按数据类型过滤，如 BIGINT、VARCHAR(255)

    Returns:
        字段元数据列表，结构示例::
            {
              "success": true,
              "data": [
                {
                  "column_id": "col_001",
                  "table_name": "orders",
                  "column_name": "order_id",
                  "data_type": "BIGINT",
                  "is_primary_key": true,
                  "is_nullable": false,
                  "default_value": null,
                  "comment": "订单ID",
                  "ordinal_position": 1
                }
              ],
              "total": 1
            }
    """
    params: Dict[str, Any] = {"table_name": table_name}
    if column_name:
        params["column_name"] = column_name
    if data_type:
        params["data_type"] = data_type
    # TODO: 根据真实后端接口调整参数与路径
    return await _request("GET", "/metadata/fields", params=params)


@mcp.tool()
async def query_data_lineage(
    target_table: str = "",
    target_column: str = "",
    direction: str = "upstream",
    depth: int = 3,
) -> Dict[str, Any]:
    """查询数据血缘关系链路，支持上游追溯和下游影响分析。

    Args:
        target_table: 目标表名
        target_column: 目标列名；提供时进行字段级血缘，否则为表级血缘
        direction: 血缘方向，upstream（上游来源）或 downstream（下游影响）
        depth: 递归深度，默认 3 层

    Returns:
        血缘关系图，结构示例::
            {
              "success": true,
              "data": {
                "root": {"table": "orders", "column": "order_id"},
                "nodes": [
                  {"table": "raw_orders", "column": "id", "level": 1},
                  {"table": "stg_orders", "column": "order_id", "level": 2}
                ],
                "edges": [
                  {"source": "raw_orders.id", "target": "stg_orders.order_id", "transform": "CAST(id AS BIGINT)"}
                ]
              }
            }
    """
    params: Dict[str, Any] = {
        "direction": direction,
        "depth": depth,
    }
    if target_table:
        params["target_table"] = target_table
    if target_column:
        params["target_column"] = target_column
    # TODO: 根据真实血缘接口调整参数与返回结构
    return await _request("GET", "/lineage/query", params=params)


@mcp.tool()
async def manage_transformation_rules(
    action: str = "list",
    rule_id: str = "",
    source_table: str = "",
    source_column: str = "",
    target_table: str = "",
    target_column: str = "",
    transform_expression: str = "",
    description: str = "",
) -> Dict[str, Any]:
    """管理字段转换规则：列出、创建、更新、删除转换规则。

    Args:
        action: 操作类型，可选 list / create / update / delete
        rule_id: 规则 ID（update / delete 时必填）
        source_table: 源表名
        source_column: 源列名
        target_table: 目标表名
        target_column: 目标列名
        transform_expression: 转换表达式，如 CAST(id AS BIGINT) 或 COALESCE(val, 0)
        description: 规则描述

    Returns:
        操作结果，结构示例::
            {
              "success": true,
              "data": {
                "rule_id": "rule_001",
                "source": "raw_orders.id",
                "target": "stg_orders.order_id",
                "expression": "CAST(id AS BIGINT)",
                "description": "类型转换"
              },
              "message": "Transformation rule created successfully"
            }
    """
    payload: Dict[str, Any] = {
        "source_table": source_table,
        "source_column": source_column,
        "target_table": target_table,
        "target_column": target_column,
        "transform_expression": transform_expression,
        "description": description,
    }
    # TODO: 根据真实后端接口调整 payload 字段
    if action == "list":
        params = {k: v for k, v in payload.items() if v}
        return await _request("GET", "/transformation-rules", params=params)
    elif action == "create":
        return await _request("POST", "/transformation-rules", json=payload)
    elif action == "update":
        if not rule_id:
            return {"success": False, "error": "rule_id is required for update"}
        return await _request("PUT", f"/transformation-rules/{rule_id}", json=payload)
    elif action == "delete":
        if not rule_id:
            return {"success": False, "error": "rule_id is required for delete"}
        return await _request("DELETE", f"/transformation-rules/{rule_id}")
    else:
        return {"success": False, "error": f"Unknown action: {action}"}


@mcp.tool()
async def search_metadata(
    keyword: str,
    resource_type: str = "",
    limit: int = 20,
    offset: int = 0,
) -> Dict[str, Any]:
    """按关键词全文搜索元数据（表名、列名、注释、规则描述等）。

    Args:
        keyword: 搜索关键词
        resource_type: 资源类型过滤，可选 table / column / rule / lineage
        limit: 返回条数上限，默认 20
        offset: 分页偏移，默认 0

    Returns:
        搜索结果列表，结构示例::
            {
              "success": true,
              "data": [
                {"type": "table", "name": "orders", "description": "订单主表", "url": "/metadata/tables/tbl_001"},
                {"type": "column", "name": "order_id", "description": "订单ID", "url": "/metadata/fields/col_001"}
              ],
              "total": 2,
              "keyword": "order"
            }
    """
    params: Dict[str, Any] = {
        "keyword": keyword,
        "limit": limit,
        "offset": offset,
    }
    if resource_type:
        params["resource_type"] = resource_type
    # TODO: 根据真实搜索接口调整参数
    return await _request("GET", "/metadata/search", params=params)


# ── 入口 ──────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="stdio")
