"""一条可重复运行的检查：从上传 ZIP 启动真实 stdio 服务并调用所有工具。"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from zipfile import ZipFile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package", ROOT / "scripts/package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


@asynccontextmanager
async def session(server: Path, cwd: Path, env: dict):
    params = StdioServerParameters(command=sys.executable, args=[str(server)], cwd=str(cwd), env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=10)) as client:
            await client.initialize()
            yield client


async def call(client, name, **args):
    result = await client.call_tool(name, args)
    assert not result.isError, f"MCP 协议调用失败 {name}: {result.content}"
    return result.structuredContent or json.loads(result.content[0].text)


async def check():
    artifacts = package.build()
    assert len(artifacts) == 7
    for path in artifacts[:5]:
        with ZipFile(path) as z:
            assert "SKILL.md" in z.namelist()
            assert all(not p.startswith((".venv/", "/")) and ".." not in p.split("/") for p in z.namelist())
    with ZipFile(artifacts[-1]) as z:
        assert set(z.namelist()) == {"manifest.json", "fde-assistant-source.zip"}
        assert z.read("fde-assistant-source.zip") == artifacts[-2].read_bytes()
        assert json.loads(z.read("manifest.json"))["typ"] == "mcp_script"

    with tempfile.TemporaryDirectory(prefix="fde-check-") as tmp:
        work = Path(tmp)
        extracted = work / "uploaded"
        with ZipFile(artifacts[-2]) as z:
            assert set(z.namelist()) == {"server.py", "requirements.txt", "catalog.json"}
            z.extractall(extracted)
        server = extracted / "server.py"
        for p in extracted.iterdir():
            assert p.read_bytes() == (ROOT / "tools/fde-assistant-mcp" / p.name).read_bytes()
        env = {"FDE_STATE_DB": "", "FDE_WORKSPACE_ID": "", "PYTHONDONTWRITEBYTECODE": "1"}
        # 不在脚本目录启动，验证数据路径使用 __file__ 而非依赖工作目录。
        async with session(server, work, env) as client:
            listed = await client.list_tools()
            assert {t.name for t in listed.tools} == {"search_products", "get_product", "lookup_collaboration", "load_project", "update_project"}
            assert next(t for t in listed.tools if t.name == "update_project").annotations.destructiveHint
            result = await call(client, "search_products", keywords=["违停", "派单"])
            assert result["demo"] and {r["product"]["id"] for r in result["data"]} == {"DEMO-P001", "DEMO-P002"}
            assert result["warnings"]
            positive = await call(client, "search_products", keywords=["车辆识别"])
            assert [r["product"]["id"] for r in positive["data"]] == ["DEMO-P002"]
            assert not (await call(client, "search_products", keywords=["车辆识别"], audience="external"))["data"]
            combined = await call(client, "search_products", keywords=["车辆识别", "事件派单", "平台展示"])
            assert len(combined["data"]) == 3
            external = await call(client, "get_product", product_id="DEMO-P001", audience="external")
            assert set(external["data"]) == {"id", "name", "public"}
            assert "internal_notes" not in json.dumps(external)
            internal = await call(client, "get_product", product_id="DEMO-P001")
            assert internal["data"]["source"]["reference"].startswith("demo:")
            depts = await call(client, "lookup_collaboration", capabilities=["处置", "车辆", "展示"])
            assert len(depts["data"]) == 3 and depts["visibility"] == "internal"
            assert not (await call(client, "search_products", keywords=["量子卫星"]))["data"]
            assert (await call(client, "get_product", product_id="unknown"))["error"]["code"] == "NOT_FOUND"
            assert (await call(client, "search_products", keywords=[]))["error"]["code"] == "INVALID_ARGUMENT"
            assert (await call(client, "search_products", keywords=["车辆"], limit=21))["error"]["code"] == "INVALID_ARGUMENT"
            assert (await call(client, "load_project", project_id="park"))["error"]["code"] == "STATE_DISABLED"
            assert (await call(client, "update_project", project_id="park", project={}, expected_revision=0))["error"]["code"] == "STATE_DISABLED"
        print("PASS: ZIP 结构、stdio 初始化、五个工具注册、产品与跨部门查询、字段裁剪、缺省关闭记录")

        db = work / "state" / "projects.sqlite3"
        env.update(FDE_STATE_DB=str(db), FDE_WORKSPACE_ID="team-a")
        card = {
            "title": "模拟园区项目", "stage": "需求澄清", "questions": ["接口是否可用？"],
            "facts": [{"id": "F1", "text": "已有 6 台验证结果", "kind": "progress", "source_ref": "模拟测试记录", "confirmation": "confirmed", "confirmed_by": "演示评审人", "verification": "verified", "evidence": "模拟用例 1–6", "visibility": "external"}],
            "actions": [{"text": "申请权限", "owner": "", "due": "", "status": "proposed", "evidence": ""}],
        }
        async with session(server, work, env) as client:
            assert (await call(client, "load_project", project_id="park"))["error"]["code"] == "NOT_FOUND"
            saved = await call(client, "update_project", project_id="park", project=card, expected_revision=0)
            assert saved["ok"] and saved["revision"] == 1
            assert (await call(client, "update_project", project_id="park", project=card, expected_revision=0))["already_saved"]
        # 新进程读回，验证不是仅存在服务进程内存中。
        async with session(server, work, env) as client:
            loaded = await call(client, "load_project", project_id="park")
            assert loaded["project"] == card and loaded["revision"] == 1
            left, right = deepcopy(card), deepcopy(card)
            left["stage"], right["stage"] = "方案讨论", "补充调研"
            results = await asyncio.gather(
                call(client, "update_project", project_id="park", project=left, expected_revision=1),
                call(client, "update_project", project_id="park", project=right, expected_revision=1),
            )
            assert sum(r["ok"] for r in results) == 1
            assert [r["error"]["code"] for r in results if not r["ok"]] == ["REVISION_CONFLICT"]
            invalid = deepcopy(card)
            del invalid["facts"][0]["evidence"]
            assert (await call(client, "update_project", project_id="park", project=invalid, expected_revision=2))["error"]["code"] == "INVALID_ARGUMENT"
            invalid = deepcopy(card)
            invalid["actions"][0]["status"] = "done"
            assert (await call(client, "update_project", project_id="park", project=invalid, expected_revision=2))["error"]["code"] == "INVALID_ARGUMENT"
            assert (await call(client, "load_project", project_id="park"))["revision"] == 2
            assert (await call(client, "load_project", project_id="../park"))["error"]["code"] == "INVALID_ARGUMENT"
        env["FDE_WORKSPACE_ID"] = "team-b"
        async with session(server, work, env) as client:
            assert (await call(client, "load_project", project_id="park"))["error"]["code"] == "NOT_FOUND"
        print("PASS: 保存后重启读取、重复提交、并发版本冲突、证据校验、工作空间配置隔离")

        data = json.loads((extracted / "catalog.json").read_text())
        data["products"][0]["source"]["valid_until"] = "2000-01-01"
        altered = work / "catalog.json"
        altered.write_text(json.dumps(data, ensure_ascii=False))
        env["FDE_CATALOG_PATH"] = str(altered)
        async with session(server, work, env) as client:
            result = await call(client, "get_product", product_id="DEMO-P001")
            assert any("过期" in w for w in result["warnings"])
            data["products"][0]["source"]["updated_at"] = "20260919"
            altered.write_text(json.dumps(data))
            assert (await call(client, "get_product", product_id="DEMO-P001"))["error"]["code"] == "CATALOG_INVALID"
            data["products"][0]["source"]["updated_at"] = "2026-09-19"
            altered.write_text("{invalid")
            assert (await call(client, "get_product", product_id="DEMO-P001"))["error"]["code"] == "CATALOG_INVALID"
            data["demo"] = False
            altered.write_text(json.dumps(data))
            assert (await call(client, "get_product", product_id="DEMO-P001"))["error"]["code"] == "CATALOG_INVALID"
        print("PASS: 资料过期提示、坏目录失败、演示来源不得伪装正式目录")
    print("全部自动检查通过。Skill 的平台模型行为与公司平台上传仍需单独验证。")


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(check(), timeout=90))
