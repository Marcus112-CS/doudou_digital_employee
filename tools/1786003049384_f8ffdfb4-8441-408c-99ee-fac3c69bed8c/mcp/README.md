# 元数据管理服务 MCP Server

统一管理数据资产元数据：表结构、字段定义、数据类型、转换规则、血缘关系链路。
提供元数据查询和血缘追溯接口，是字段血缘分析的基础数据层。

## 工具列表

| 工具名 | 功能说明 |
|---|---|
| `query_table_metadata` | 查询表元数据（表结构、列定义、存储属性） |
| `query_field_metadata` | 查询字段/列元数据（数据类型、主键、默认值、注释） |
| `query_data_lineage` | 查询数据血缘关系链路（上游追溯 / 下游影响） |
| `manage_transformation_rules` | 管理字段转换规则（列出 / 创建 / 更新 / 删除） |
| `search_metadata` | 按关键词全文搜索元数据 |

## 环境变量

| 变量名 | 说明 | 默认值 |
|---|---|---|
| `METADATA_API_BASE_URL` | 元数据管理后端 API 地址 | `http://localhost:8080/api/v1` |
| `METADATA_API_TOKEN` | 鉴权 Bearer Token | 空字符串 |
| `METADATA_REQUEST_TIMEOUT` | 请求超时秒数 | `30` |

## 安装与启动

```bash
# 安装依赖
pip install -r requirements.txt

# 启动（stdio 传输）
python server.py

# 或使用 uv
uv run server.py
```

## 接口说明

所有工具通过 stdio 与 MCP Client 通信，内部使用 httpx 调用元数据管理后端 REST API。

> **注意**：当后端 API 未就绪时，工具仍可启动并返回占位错误响应，不会阻塞 MCP 连接。

## TODO

- [ ] 对接真实元数据管理后端接口，调整路径与参数命名
- [ ] 补充鉴权方式（API Key / OAuth2）
- [ ] 按实际血缘图结构调整 nodes / edges 返回格式
- [ ] 增加缓存层提升高频查询性能
