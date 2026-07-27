#!/usr/bin/env python3
"""
首次发布脚本：读取 first_post.md，生成 HTML 并发布
这是一个一次性脚本，用于手动生成第一篇博客
"""

import json
import re
import subprocess
import sys
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_post import (
    markdown_to_html, build_post_html, write_post_files,
    update_index_html, git_commit_and_push
)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

def main():
    md_file = SCRIPT_DIR / "first_post.md"
    if not md_file.exists():
        print(f"[ERROR] 找不到 {md_file}")
        sys.exit(1)

    md_content = md_file.read_text(encoding="utf-8")

    # 提取第一行的标题
    title_match = re.search(r'^#\s+(.+)$', md_content, re.MULTILINE)
    post_title = title_match.group(1) if title_match else "Java线程池深度解析"

    print(f"[标题] {post_title}")

    # Markdown → HTML
    content_html = markdown_to_html(md_content)

    # 生成完整页面
    full_html = build_post_html(
        title=post_title,
        category="Java",
        tags=["Java", "JVM", "Spring", "并发编程"],
        date_str="2026-07-27",
        content_html=content_html,
        cover_img="/img/3063.jpg"
    )

    # 写入文章目录
    url_path = write_post_files(post_title, full_html)

    # 提取纯文本摘要
    plain_text = re.sub(r'<[^>]+>', '', content_html)
    plain_text = re.sub(r'\s+', ' ', plain_text).strip()[:200]

    # 更新首页
    update_index_html(
        post_title=post_title,
        post_url_path=url_path,
        date_str="2026-07-27",
        content_preview=plain_text,
        category="Java",
        cover_img="/img/3063.jpg"
    )

    # Git 提交推送
    print("\n[Git] 准备提交到 GitHub...")
    success = git_commit_and_push(post_title)

    if success:
        print(f"\n✅ 第一篇博客发布成功！")
        print(f"   标题: {post_title}")
        print(f"   访问: https://xiaozeng26.github.io{url_path}")
    else:
        print("\n[INFO] 无变更或推送跳过")

if __name__ == "__main__":
    main()
