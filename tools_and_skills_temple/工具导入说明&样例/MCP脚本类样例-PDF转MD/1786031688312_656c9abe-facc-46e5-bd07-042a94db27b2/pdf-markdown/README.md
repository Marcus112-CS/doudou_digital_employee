# PDF转Markdown MCP Server

将PDF文件通过HTTP/HTTPS URL下载并转换为Markdown格式(.md)的MCP工具。

## 功能特性

- 通过URL下载PDF文件
- 使用PyMuPDF引擎提取文本内容
- 使用markdownify转换为Markdown格式
- 仅包含纯文本内容，不包含图片
- 支持自定义输出目录和文件名
- 自动清理临时文件
- 文件大小限制50MB

## 安装

```bash
cd {{remoteProjectPath}}
pip install -r requirements.txt
```

## 使用方式

### 启动服务

```bash
cd {{remoteProjectPath}}
python server.py
```

服务通过stdio通信，可配置到支持MCP的客户端中。

### MCP工具参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| url | string | 是 | PDF文件的HTTP/HTTPS URL地址 |
| output_dir | string | 否 | Markdown文件输出目录，默认为系统临时目录 |
| output_filename | string | 否 | 输出文件名，默认自动生成 |

### 返回格式

```
转换成功|输出路径:/path/to/output.md|文件大小:123.4KB
```

或

```
转换失败:错误信息
```

## 依赖

- mcp >= 1.6.0
- PyMuPDF >= 1.24.0
- markdownify >= 0.13.1
- requests >= 2.32.3

## 注意事项

- 仅支持http://或https://开头的URL
- 文件大小限制为50MB
- 转换过程中会下载PDF到临时目录，完成后自动清理
- 仅提取纯文本内容，不包含图片
- 扫描版PDF可能无法正确识别文字内容