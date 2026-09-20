# 部门职责映射工具 MCP Server

维护事件类型与处理部门之间映射关系的 MCP 工具。

## 功能

- `add_dept_mapping`: 添加事件类型与处理部门的映射关系
- `query_dept_mapping`: 查询事件类型对应的处理部门
- `list_all_mappings`: 列出所有事件类型与部门的映射关系
- `delete_dept_mapping`: 删除指定的事件类型与部门映射关系

## 环境变量配置

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| DEPT_MAPPING_API_ENDPOINT | 外部 API 端点地址 | http://localhost:8080/api/dept-mapping |
| DEPT_MAPPING_API_KEY | API 鉴权密钥 | default_api_key_placeholder |

## 启动方式

```bash
# 使用 uv 运行
uv run python server.py

# 或使用标准 Python
pip install -r requirements.txt
python server.py
```

## 集成到 Hermes

在 config.yaml 中添加 MCP 服务器配置：

```yaml
mcp:
  servers:
    dept-mapping-tool:
      command: python
      args:
        - /path/to/server.py
      env:
        DEPT_MAPPING_API_ENDPOINT: http://your-api-endpoint
        DEPT_MAPPING_API_KEY: your-api-key
```

## 注意事项

- 当前版本使用内存存储，重启后数据会丢失
- 生产环境请替换为数据库持久化存储
- 需要在 TODO 标记处补充外部 API 调用逻辑
