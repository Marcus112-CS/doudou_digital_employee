"""校验公司平台 Skill 结构，构建五个 Skill ZIP 和两种 Tool ZIP。"""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import ast
import json
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "fde-previsit-research", "fde-project-facts", "fde-audience-communication",
    "fde-product-matching", "fde-collaboration-routing",
)


def skill_files(folder: Path) -> list[Path]:
    entry = folder / "SKILL.md"
    content = entry.read_text(encoding="utf-8")
    assert content.startswith("---\n"), f"缺少 frontmatter: {entry}"
    meta = yaml.safe_load(content.split("---", 2)[1])
    assert meta["name"] == folder.name
    assert isinstance(meta["description"], str) and meta["description"].strip()
    assert not re.search(r'[/\\:*?"<>|]', meta["name"])
    if "version" in meta:
        assert re.fullmatch(r"\d+\.\d+\.\d+", str(meta["version"]))
    files = sorted(folder.rglob("*.md"))
    assert sum(p.name.lower() == "skill.md" for p in files) == 1
    for p in files:
        value = p.read_text(encoding="utf-8")
        for ref in re.findall(r"\]\(([^)]+)\)", value):
            assert "://" not in ref, f"Skill 含未经配置的外部链接: {ref}"
            target = (p.parent / ref).resolve()
            assert target.is_relative_to(folder.resolve()) and target.is_file(), f"辅助文件缺失: {target}"
    return files


def build() -> list[Path]:
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    artifacts = []
    for name in SKILLS:
        folder = ROOT / "skills" / name
        target = out / (name + ".zip")
        with ZipFile(target, "w", ZIP_DEFLATED) as z:
            for p in skill_files(folder):
                z.write(p, p.relative_to(folder).as_posix())
        assert target.stat().st_size <= 100_000_000
        artifacts.append(target)
    source = ROOT / "tools" / "fde-assistant-mcp"
    ast.parse((source / "server.py").read_text())
    json.loads((source / "catalog.json").read_text())
    source_zip = out / "fde-assistant-source.zip"
    with ZipFile(source_zip, "w", ZIP_DEFLATED) as z:
        for name in ("server.py", "requirements.txt", "catalog.json"):
            z.write(source / name, name)
    command = json.loads((ROOT / "platform" / "tool-command.json").read_text())
    assert command == {"mcpServers": {"env": {}, "args": ["server.py"], "command": "python", "transport": "stdio"}}
    manifest = {
        "name": "FDE目录与项目记录", "typ": "mcp_script", "industry": "通用",
        "description": "本地产品和协同目录查询，可选项目记录；附带数据均为演示。",
        "config": {"cmd": command},
    }
    exchange = out / "fde-assistant-import.zip"
    with ZipFile(exchange, "w", ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.write(source_zip, source_zip.name)
    assert exchange.stat().st_size <= 200_000_000
    artifacts.extend((source_zip, exchange))
    return artifacts


if __name__ == "__main__":
    for artifact in build():
        print(f"{artifact.relative_to(ROOT)} ({artifact.stat().st_size} bytes)")
