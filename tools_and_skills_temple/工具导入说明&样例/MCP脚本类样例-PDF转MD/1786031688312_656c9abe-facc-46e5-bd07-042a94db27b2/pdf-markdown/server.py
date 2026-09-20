"""
PDF转Markdown MCP Server
支持通过HTTP/HTTPS URL下载PDF文件，并使用PyMuPDF提取文本转换为Markdown格式
"""
import os
import uuid
import tempfile
import requests
from typing import Optional

from mcp.server.fastmcp import FastMCP
import fitz  # PyMuPDF
from markdownify import markdownify as md

mcp = FastMCP(
    name="pdf-to-markdown",
    description="将PDF文件通过URL下载并转换为Markdown格式的工具"
)

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB


def _download_pdf(url: str, save_path: str) -> str:
    """从URL下载PDF文件到本地"""
    try:
        response = requests.get(url, stream=True, timeout=60, headers={
            "User-Agent": "Mozilla/5.0 (compatible; PDF-Markdown-MCP/1.0)"
        })
        response.raise_for_status()
        
        content_length = response.headers.get("Content-Length")
        if content_length and int(content_length) > MAX_FILE_SIZE:
            raise Exception(f"文件过大: {int(content_length) / 1024 / 1024:.1f}MB，限制50MB以内")
        
        with open(save_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        return save_path
    except requests.exceptions.RequestException as e:
        raise Exception(f"下载失败: {str(e)}")


def _extract_text_from_pdf(pdf_path: str) -> str:
    """使用PyMuPDF提取PDF文本内容"""
    try:
        doc = fitz.open(pdf_path)
        full_text = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                full_text.append(f"

## 第 {page_num + 1} 页

")
                full_text.append(text)
        
        doc.close()
        return "
".join(full_text)
    except Exception as e:
        raise Exception(f"PDF文本提取失败: {str(e)}")


def _convert_to_markdown(text: str) -> str:
    """将提取的文本转换为Markdown格式"""
    try:
        # 使用markdownify处理文本，保持基本格式
        markdown_text = md(text, heading_style="ATX", strip=['img'])
        return markdown_text
    except Exception as e:
        raise Exception(f"Markdown转换失败: {str(e)}")


@mcp.tool()
def pdf_to_markdown(
    url: str,
    output_dir: Optional[str] = None,
    output_filename: Optional[str] = None
) -> str:
    """
    将PDF文件从HTTP/HTTPS URL下载并转换为Markdown格式。
    使用PyMuPDF提取文本，仅包含纯文本内容，不包含图片。
    
    Args:
        url: PDF文件的HTTP/HTTPS URL地址
        output_dir: 可选，Markdown文件输出目录，默认为系统临时目录
        output_filename: 可选，输出文件名，默认自动生成
    
    Returns:
        转换结果信息，包含文件路径和大小
    """
    pdf_path = None
    md_path = None
    
    try:
        if not url.startswith(("http://", "https://")):
            return "错误: URL必须以http://或https://开头"
        
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            work_dir = output_dir
        else:
            work_dir = tempfile.gettempdir()
        
        pdf_path = os.path.join(work_dir, f"temp_pdf_{uuid.uuid4().hex[:8]}.pdf")
        
        _download_pdf(url, pdf_path)
        
        if output_filename:
            if not output_filename.lower().endswith(".md"):
                output_filename += ".md"
            md_path = os.path.join(work_dir, output_filename)
        else:
            md_path = os.path.join(work_dir, f"converted_{uuid.uuid4().hex[:8]}.md")
        
        # 提取PDF文本
        extracted_text = _extract_text_from_pdf(pdf_path)
        
        # 转换为Markdown
        markdown_content = _convert_to_markdown(extracted_text)
        
        # 写入Markdown文件
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)
        
        md_size = os.path.getsize(md_path)
        
        return f"转换成功|输出路径:{md_path}|文件大小:{md_size / 1024:.1f}KB"
        
    except Exception as e:
        return f"转换失败:{str(e)}"
    finally:
        if pdf_path and os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
            except Exception:
                pass


if __name__ == "__main__":
    mcp.run(transport="stdio")