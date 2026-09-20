"""FDE 目录查询及可选项目记录。仅 stdio；一个实例对应一个受信任工作空间。"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date, datetime, timezone
from contextlib import closing
from functools import wraps
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

BASE = Path(__file__).resolve().parent
CATALOG = Path(os.environ.get("FDE_CATALOG_PATH", str(BASE / "catalog.json")))
DB_PATH = os.environ.get("FDE_STATE_DB", "")
WORKSPACE = os.environ.get("FDE_WORKSPACE_ID", "")
mcp = FastMCP("fde-assistant")
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require(condition: bool, message: str, code: str = "INVALID_ARGUMENT") -> None:
    if not condition:
        raise ToolError(code, message)


def guarded(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": str(exc)}}
        except (OSError, sqlite3.Error):
            return {"ok": False, "error": {"code": "STORAGE_ERROR", "message": "目录或记录存储不可用，请管理员检查配置与权限；未确认写入成功。"}}
    return call


def text(value: Any, label: str, maximum: int = 2000, allow_empty: bool = False) -> str:
    require(isinstance(value, str) and len(value) <= maximum, f"{label} 必须为不超过 {maximum} 字的字符串")
    require(allow_empty or bool(value.strip()), f"{label} 不能为空")
    return value


def strings(value: Any, label: str, maximum: int = 50) -> list[str]:
    require(isinstance(value, list) and len(value) <= maximum, f"{label} 必须为最多 {maximum} 项的列表")
    for item in value:
        text(item, label)
    return value


def source_check(source: Any) -> None:
    require(isinstance(source, dict), "source 必须为对象", "CATALOG_INVALID")
    for key in ("title", "reference", "updated_at"):
        text(source.get(key), "source." + key)
    require(source.get("visibility") in ("internal", "public"), "source.visibility 应为 internal/public")
    try:
        for key in ("updated_at", "valid_until"):
            value = source.get(key)
            if value is not None:
                require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value, "来源日期必须使用 YYYY-MM-DD", "CATALOG_INVALID")
    except (ValueError, TypeError):
        raise ToolError("CATALOG_INVALID", "来源日期必须使用 YYYY-MM-DD")


def catalog() -> dict:
    try:
        require(CATALOG.stat().st_size <= 5_000_000, "目录超过 5 MB", "CATALOG_INVALID")
        data = json.loads(CATALOG.read_text(encoding="utf-8"))
        require(isinstance(data, dict) and data.get("schema_version") == 1, "目录 schema_version 必须为 1")
        require(type(data.get("demo")) is bool, "目录必须显式填写 demo 布尔值")
        for group in ("products", "departments"):
            rows = data.get(group)
            require(isinstance(rows, list) and len(rows) <= 1000, f"{group} 最多 1000 项")
            ids = set()
            for row in rows:
                require(isinstance(row, dict), "目录记录必须为对象")
                for key in ("id", "name"):
                    text(row.get(key), key, 100)
                require(row["id"] not in ids, "目录存在重复 ID")
                ids.add(row["id"])
                strings(row.get("keywords"), "keywords")
                source_check(row.get("source"))
                if not data["demo"]:
                    require(not row["source"]["reference"].startswith("demo:"), "正式目录不能沿用演示来源")
                if group == "products":
                    public = row.get("public")
                    require(isinstance(public, dict), "产品缺少 public 内容")
                    text(public.get("summary"), "public.summary")
                    for key in ("capabilities", "prerequisites", "exclusions"):
                        strings(public.get(key), "public." + key)
                    strings(row.get("owner_department_ids"), "owner_department_ids")
                    text(row.get("internal_notes", ""), "internal_notes", allow_empty=True)
                else:
                    for key in ("responsibilities", "exclusions", "required_materials"):
                        strings(row.get(key), key)
                    for key in ("entrypoint", "cooperation_notes"):
                        text(row.get(key), key)
        department_ids = {r["id"] for r in data["departments"]}
        require(all(set(r["owner_department_ids"]) <= department_ids for r in data["products"]), "产品引用了不存在的部门 ID")
        return data
    except (ToolError, ValueError, UnicodeError) as exc:
        raise ToolError("CATALOG_INVALID", f"目录格式不合法：{exc}") from exc


def warnings(data: dict, rows: list[dict]) -> list[str]:
    notes = ["演示数据：产品、团队和分工均为模拟，不能代表公司真实能力或职责。"] if data["demo"] else []
    today = date.today().isoformat()
    for row in rows:
        source = row["source"]
        expiry = source.get("valid_until")
        if source["updated_at"] > today:
            notes.append(f"{row['id']} 来源日期在未来，需核实。")
        if not expiry:
            notes.append(f"{row['id']} 未设置有效期，使用前请核实时效。")
        elif expiry < today:
            notes.append(f"{row['id']} 资料已过期，不应据此作当前能力或职责承诺。")
    return notes


def product_view(row: dict, audience: str) -> dict:
    require(audience in ("internal", "external"), "audience 应为 internal/external")
    if audience == "internal":
        return row
    result = {"id": row["id"], "name": row["name"], "public": row["public"]}
    if row["source"]["visibility"] == "public":
        result["source"] = row["source"]
    return result


def match(rows: list[dict], keywords: list[str], limit: int, fields: tuple[str, ...]) -> list[dict]:
    strings(keywords, "keywords", 12)
    require(bool(keywords) and all(len(k) <= 64 for k in keywords), "提供 1–12 个、每个最多 64 字的关键词")
    require(type(limit) is int and 1 <= limit <= 20, "limit 应为 1–20")
    terms = list(dict.fromkeys(k.strip().casefold() for k in keywords))
    found = []
    # ponytail: 最多 1000 条目录采用子串检索；规模或召回不足时接平台检索 API。
    for row in rows:
        values = []
        for field in fields:
            value = row
            for key in field.split("."):
                value = value.get(key) if isinstance(value, dict) else None
            values.append(value)
        haystack = json.dumps(values, ensure_ascii=False).casefold()
        hits = [k for k in terms if k in haystack]
        if hits:
            found.append({"record": row, "matched_keywords": hits})
    found.sort(key=lambda r: (-len(r["matched_keywords"]), r["record"]["id"]))
    return found[:limit]


@mcp.tool(annotations=READ)
@guarded
def search_products(keywords: list[str], audience: str = "internal", limit: int = 5) -> dict:
    """按 1–12 个业务关键词查产品候选。命中不代表适配；核对 prerequisites/exclusions/source。
    audience=external 仅返回已放入 public 字段的内容；这是输出裁剪，不是身份鉴权。
    """
    data = catalog()
    require(audience in ("internal", "external"), "audience 应为 internal/external")
    # 不从限制和“不支持”字段召回，避免把明确不具备的能力当作匹配。
    fields = ("name", "public.summary", "public.capabilities")
    if audience == "internal":
        fields += ("keywords",)
    hits = match(data["products"], keywords, limit, fields)
    return {"ok": True, "demo": data["demo"], "data": [
        {"product": product_view(h["record"], audience), "matched_keywords": h["matched_keywords"]} for h in hits
    ], "warnings": warnings(data, [h["record"] for h in hits]), "match_method": "关键词子串匹配，排序不是适配概率；未命中不代表公司没有该能力。"}


@mcp.tool(annotations=READ)
@guarded
def get_product(product_id: str, audience: str = "internal") -> dict:
    """按 search_products 返回的 ID 读取产品详情、前提、限制及来源。"""
    text(product_id, "product_id", 100)
    data = catalog()
    row = next((p for p in data["products"] if p["id"] == product_id), None)
    require(row is not None, "未找到该产品，请核对 ID", "NOT_FOUND")
    return {"ok": True, "demo": data["demo"], "data": product_view(row, audience), "warnings": warnings(data, [row])}


@mcp.tool(annotations=READ)
@guarded
def lookup_collaboration(capabilities: list[str], limit: int = 8) -> dict:
    """按拆分后的能力关键词查多个内部协同部门；返回职责、边界、入口、材料及来源。
    结果仅供内部 FDE；不决定项目牵头、不联系任何人、不代表协作方接单。
    """
    data = catalog()
    hits = match(data["departments"], capabilities, limit, ("name", "keywords", "responsibilities"))
    return {"ok": True, "demo": data["demo"], "visibility": "internal", "data": hits,
            "warnings": warnings(data, [h["record"] for h in hits]), "decision": "候选协同方，职责重叠与项目归属需人工确认。"}


def state_config() -> Path:
    require(bool(DB_PATH and WORKSPACE), "项目记录未启用。由管理员配置 FDE_STATE_DB 和 FDE_WORKSPACE_ID，或使用平台原生记录。", "STATE_DISABLED")
    path = Path(DB_PATH)
    require(path.is_absolute() and str(path) != ":memory:", "FDE_STATE_DB 必须为平台允许的持久化绝对文件路径", "STATE_CONFIG_INVALID")
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", WORKSPACE)), "FDE_WORKSPACE_ID 格式不合法", "STATE_CONFIG_INVALID")
    return path


def project_id_check(project_id: str) -> None:
    require(isinstance(project_id, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project_id)), "project_id 仅支持 1–64 位字母、数字、下划线和短横线")


def project_check(project: dict) -> str:
    require(isinstance(project, dict), "project 必须为对象")
    required = {"title", "stage", "facts", "questions", "actions"}
    require(required <= project.keys() and project.keys() <= required | {"customer"}, "项目字段为 title/stage/facts/questions/actions，可选 customer；保存时提交完整快照")
    text(project["title"], "title", 200)
    text(project["stage"], "stage", 100)
    text(project.get("customer", ""), "customer", 200, allow_empty=True)
    strings(project["questions"], "questions")
    for group in ("facts", "actions"):
        require(isinstance(project[group], list) and len(project[group]) <= 200, f"{group} 最多 200 项")
    ids = set()
    for fact in project["facts"]:
        require(isinstance(fact, dict), "fact 必须为对象")
        keys = {"id", "text", "kind", "source_ref", "confirmation", "verification", "visibility"}
        require(keys <= fact.keys() and fact.keys() <= keys | {"confirmed_by", "evidence"}, "fact 字段不完整或有未知字段，参照项目事实 Skill 的格式")
        for key in ("id", "text", "source_ref"):
            text(fact[key], "fact." + key)
        require(fact["id"] not in ids, "事实 ID 重复")
        ids.add(fact["id"])
        require(fact["kind"] in ("requirement", "progress", "decision", "external_background", "hypothesis"), "kind 不合法")
        require(fact["confirmation"] in ("confirmed", "pending", "inferred"), "confirmation 不合法")
        require(fact["verification"] in ("verified", "unverified", "not_applicable"), "verification 不合法")
        require(fact["visibility"] in ("internal", "external"), "visibility 不合法")
        for key in ("confirmed_by", "evidence"):
            if key in fact:
                text(fact[key], "fact." + key, allow_empty=True)
        if fact["confirmation"] == "confirmed":
            text(fact.get("confirmed_by"), "confirmed_by")
        if fact["verification"] == "verified":
            text(fact.get("evidence"), "evidence")
        if fact["kind"] == "hypothesis":
            require(fact["confirmation"] == "inferred", "hypothesis 必须标为 inferred")
    for action in project["actions"]:
        require(isinstance(action, dict) and set(action) == {"text", "owner", "due", "status", "evidence"}, "action 字段应为 text/owner/due/status/evidence")
        for key in ("text", "owner", "due", "evidence"):
            text(action[key], "action." + key, allow_empty=key != "text")
        require(action["status"] in ("proposed", "accepted", "done"), "action.status 不合法")
        if action["status"] != "proposed":
            text(action["owner"], "action.owner")
            text(action["evidence"], "action.evidence")
    try:
        payload = json.dumps(project, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        raise ToolError("INVALID_ARGUMENT", "project 必须是有效 JSON")
    require(len(payload.encode()) <= 200_000, "项目快照超过 200 KB")
    return payload


@mcp.tool(annotations=READ)
@guarded
def load_project(project_id: str) -> dict:
    """读取当前部署工作空间的完整项目快照与 revision。未启用持久化时返回 STATE_DISABLED。"""
    project_id_check(project_id)
    path = state_config()
    if not path.exists():
        return {"ok": False, "error": {"code": "NOT_FOUND", "message": "项目尚未建立"}}
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT revision, payload, updated_at FROM projects WHERE workspace=? AND id=?", (WORKSPACE, project_id)).fetchone()
    require(row is not None, "当前工作空间未找到项目", "NOT_FOUND")
    return {"ok": True, "project_id": project_id, "revision": row[0], "project": json.loads(row[1]), "updated_at": row[2], "visibility": "internal"}


@mcp.tool(annotations=WRITE)
@guarded
def update_project(project_id: str, project: dict[str, Any], expected_revision: int) -> dict:
    """保存用户已确认的完整项目快照。新建 expected_revision=0；更新先 load_project 并传其 revision。
    保留未变事实与待办；冲突时重新读取并人工合并，不自动覆盖。语义真实性由 FDE 确认。
    """
    project_id_check(project_id)
    require(type(expected_revision) is int and expected_revision >= 0, "expected_revision 应为非负整数")
    path = state_config()
    payload = project_check(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(path, timeout=5)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS projects (workspace TEXT, id TEXT, revision INTEGER NOT NULL, payload TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(workspace,id))")
        row = db.execute("SELECT revision,payload FROM projects WHERE workspace=? AND id=?", (WORKSPACE, project_id)).fetchone()
        revision = row[0] if row else 0
        if revision != expected_revision:
            # 相同快照的网络重试可回读成功；不同内容一律拒绝覆盖。
            if row and revision == expected_revision + 1 and json.loads(row[1]) == project:
                return {"ok": True, "project_id": project_id, "revision": revision, "already_saved": True}
            raise ToolError("REVISION_CONFLICT", f"当前 revision={revision}，请重新读取后合并")
        db.execute("INSERT INTO projects VALUES (?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,updated_at=excluded.updated_at", (WORKSPACE, project_id, revision + 1, payload, now))
    return {"ok": True, "project_id": project_id, "revision": revision + 1, "updated_at": now}


if __name__ == "__main__":
    mcp.run(transport="stdio")
