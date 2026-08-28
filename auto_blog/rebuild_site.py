#!/usr/bin/env python3
"""
博客静态重建脚本（不引入前端运行时 JS，保持原有 Hexo/Butterfly 布局不变）
========================================================================

扫描 2020/2021/2026 目录下的文章 HTML，提取元信息后，用与原模板
**完全一致**的 HTML 结构重建以下页面（只更新数据，不改布局）：

  - index.html + page/2..N（首页，15 篇/页分页）
  - archives/index.html（归档，按年份分组）
  - categories/index.html（分类列表）
  - tags/index.html（标签云）
  - categories/*/ tags/*/ archives/*/*/（分类 / 标签 / 归档月份详情页）
  - 所有页面的侧边栏（最新文章 / 分类 / 标签 / 归档 / 统计）与公告

用法：
  python rebuild_site.py              # 全量重建
  python rebuild_site.py --data-only  # 仅生成 posts.json（供 generate_post.py 调用）
"""

import json
import re
import sys
from pathlib import Path
from urllib.parse import quote

REPO_ROOT = Path(__file__).resolve().parent.parent
YEAR_DIRS = ["2020", "2021", "2026"]
PER_PAGE = 15
RECENT_NUM = 5
MONTHS_CN = ["一月", "二月", "三月", "四月", "五月", "六月",
             "七月", "八月", "九月", "十月", "十一月", "十二月"]

POSTS_JSON_PATH = REPO_ROOT / "posts.json"

ANNOUNCEMENT = (
    '👋 欢迎来到小曾博客！这里持续分享 <strong>Java / Go / Python 后端开发、'
    '云原生、AI 大模型</strong> 等技术文章，内容每日自动更新。'
    '欢迎订阅 <a href="/atom.xml" target="_blank">RSS</a> '
    '或到 <a href="/messageboard/">留言板</a> 交流～'
)


# ============================================================
# 元信息提取
# ============================================================
def parse_post_html(html, year, month, day, slug):
    title = slug
    m = re.search(r"<title>([^<]*)</title>", html)
    if m:
        title = m.group(1).strip()
        title = re.sub(r"\s*\|\s*小曾博客\s*$", "", title)

    category = ""
    m = re.search(r'class="post-meta__categories"[^>]*>\s*([^<]*?)\s*</a>', html)
    if not m:
        m = re.search(r'class="article-meta__categories"[^>]*>\s*([^<]*?)\s*</a>', html)
    if m:
        category = m.group(1).strip()

    tags = []
    m = re.search(r'<meta name="keywords" content="([^"]*)"', html)
    if m:
        tags = [t.strip() for t in m.group(1).split(",") if t.strip()]
    else:
        seen = set()
        for t in re.findall(r'href="/tags/([^/"#]+)/"', html):
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                tags.append(t)

    cover = "/img/404.jpg"
    m = re.search(r'<div class="post-bg"[^>]*background-image:\s*url\(([^)]*)\)', html)
    if m:
        cover = m.group(1).strip().strip("'\"")
    else:
        m = re.search(r'<img class="post_bg"[^>]*src="([^"]*)"', html)
        if m:
            cover = m.group(1).strip()

    excerpt = ""
    m = re.search(r'<meta name="description" content="([^"]*)"', html)
    if m:
        excerpt = re.sub(r"<[^>]+>", "", m.group(1))
        excerpt = re.sub(r"\s+", " ", excerpt).strip()
        if title and excerpt.startswith(title):
            excerpt = excerpt[len(title):].strip()
        excerpt = excerpt[:160]

    return {
        "title": title,
        "url": f"/{year}/{month}/{day}/{quote(slug, safe='')}/",
        "date": f"{year}-{month}-{day}",
        "year": int(year), "month": int(month), "day": int(day),
        "category": category,
        "tags": tags,
        "cover": cover,
        "excerpt": excerpt,
    }


def extract_posts():
    posts = []
    for year in YEAR_DIRS:
        ydir = REPO_ROOT / year
        if not ydir.is_dir():
            continue
        for month_dir in sorted(ydir.iterdir()):
            if not month_dir.is_dir():
                continue
            for day_dir in sorted(month_dir.iterdir()):
                if not day_dir.is_dir():
                    continue
                for post_dir in sorted(day_dir.iterdir()):
                    if not post_dir.is_dir():
                        continue
                    idx = post_dir / "index.html"
                    if not idx.exists():
                        continue
                    try:
                        html = idx.read_text(encoding="utf-8", errors="ignore")
                    except OSError:
                        continue
                    posts.append(parse_post_html(html, year, month_dir.name, day_dir.name, post_dir.name))
    posts.sort(key=lambda p: (p["date"], p["title"]), reverse=True)
    return posts


def build_posts_json(posts=None):
    posts = posts or extract_posts()
    POSTS_JSON_PATH.write_text(json.dumps(posts, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[OK] posts.json：{len(posts)} 篇")
    return posts


# ============================================================
# 统计
# ============================================================
def category_counts(posts):
    d = {}
    for p in posts:
        if p["category"]:
            d[p["category"]] = d.get(p["category"], 0) + 1
    return d


def tag_counts(posts):
    d = {}
    for p in posts:
        for t in p["tags"]:
            if t:
                d[t] = d.get(t, 0) + 1
    return d


def month_counts(posts):
    d = {}
    for p in posts:
        k = f"{p['year']}-{p['month']:02d}"
        d[k] = d.get(k, 0) + 1
    return d


def _color(s):
    h = 0
    for ch in s:
        h = ord(ch) + ((h << 5) - h)
        h &= 0xFFFFFFFF
    r = (h % 156) + 40
    g = ((h >> 8) % 156) + 40
    b = ((h >> 16) % 156) + 40
    return f"rgb({r}, {g}, {b})"


def _tag_size(count, maxc):
    return 14 + round((count / maxc) * 15)


# ============================================================
# HTML 片段（结构与原模板完全一致）
# ============================================================
def build_card(p, side):
    cat = ""
    if p["category"]:
        cat = ('<span class="article-meta"><span class="article-meta__separator">|</span>'
               '<i class="fa fa-inbox article-meta__icon" aria-hidden="true"></i>'
               f'<a class="article-meta__categories" href="/categories/{quote(p["category"], safe="")}/">{p["category"]}</a></span>')
    content = (p["title"] + " " + p["excerpt"]).strip()[:220]
    return (
        '<div class="recent-post-item"><div class="post_cover ' + side + '">'
        f'<a href="{p["url"]}" title="{p["title"]}">     '
        f'<img class="post_bg" src="{p["cover"]}" onerror="this.onerror=null;this.src=\'/img/404.jpg\'" alt="{p["title"]}"></a></div>'
        '<div class="recent-post-info">'
        f'<a class="article-title" href="{p["url"]}" title="{p["title"]}">{p["title"]}</a>'
        '<div class="article-meta-wrap"><time class="post-meta__date">'
        f'<span class="post-meta__date-created" title="发表于 {p["date"]}"><i class="fa fa-calendar" aria-hidden="true"></i>{p["date"]}</span>'
        '<span class="article-meta__separator">|</span>'
        f'<span class="post-meta__date-updated" title="更新于 {p["date"]}"><i class="fa fa-history" aria-hidden="true"></i>{p["date"]}</span>'
        '</time>' + cat + '</div>'
        f'<div class="content">{content}</div></div></div>'
    )


def build_pagination(current, total):
    if total <= 1:
        return ""
    def href(n):
        return "/" if n == 1 else f"/page/{n}/"
    html = '<nav id="pagination"><div class="pagination">'
    if current > 1:
        html += f'<a class="extend prev" rel="prev" href="{href(current-1)}"><i class="fa fa-fw fa-chevron-left" aria-hidden="true"></i></a>'
    for n in range(1, total + 1):
        if n == current:
            html += f'<span class="page-number current">{n}</span>'
        else:
            html += f'<a class="page-number" href="{href(n)}">{n}</a>'
    if current < total:
        html += f'<a class="extend next" rel="next" href="{href(current+1)}"><i class="fa fa-fw fa-chevron-right" aria-hidden="true"></i></a>'
    html += '</div></nav>'
    return html


def build_archive(posts, title=None):
    """归档式列表：article-sort-title + article-sort（按年分组）。归档页与详情页共用。"""
    if title is None:
        title = f"文章总览 - {len(posts)}"
    html = f'<div class="article-sort-title">{title}</div><div class="article-sort">'
    cur_year = None
    for p in posts:
        if p["year"] != cur_year:
            cur_year = p["year"]
            html += f'<div class="article-sort-item year">{cur_year}</div>'
        html += (
            '<div class="article-sort-item"><div class="article-sort-img">'
            f'<a class="article-sort-item__img" href="{p["url"]}"><img src="{p["cover"]}" alt="{p["title"]}" onerror="this.onerror=null;this.src=\'/img/404.jpg\'"></a></div>'
            '<div class="article-sort-post"><a class="article-sort-item__post" href="' + p["url"] + '">'
            '<i class="fa fa-clock-o" aria-hidden="true"></i>'
            f'<time class="article-sort-item__time" title="发表于 {p["date"]}">{p["date"]}</time>'
            f'<div class="article-sort-item__title">{p["title"]}</div></a></div></div>'
        )
    html += '</div>'
    return html


def build_category_list(posts):
    cats = category_counts(posts)
    names = sorted(cats, key=lambda c: (-cats[c], c))
    html = f'<div class="category__title">分类 - <span class="category__amount">{len(names)}</span></div><div><ul class="category-list">'
    for name in names:
        html += (f'<li class="category-list-item"><a class="category-list-link" href="/categories/{quote(name, safe="")}/">{name}</a>'
                 f'<span class="category-list-count">{cats[name]}</span></li>')
    html += '</ul></div>'
    return html


def build_tag_cloud(posts):
    tags = tag_counts(posts)
    names = sorted(tags, key=lambda t: (-tags[t], t))
    maxc = max(tags.values()) if tags else 1
    html = f'<div class="tag-cloud__title">标签 - <span class="tag-cloud__amount">{len(names)}</span></div><div class="tag-cloud-tags">'
    for name in names:
        html += (f'<a href=\'/tags/{quote(name, safe="")}/\' style=\'font-size:{_tag_size(tags[name], maxc)}px; color:{_color(name)}\'>{name}</a>')
    html += '</div>'
    return html


# ============================================================
# 侧边栏
# ============================================================
def build_sidebar_recent(posts):
    items = posts[:RECENT_NUM]
    html = '<div class="card-widget card-recent-post"><div class="card-content">'
    html += '<div class="item-headline"><i class="fa fa-history" aria-hidden="true"></i><span>最新文章</span></div><div class="aside-recent-item">'
    for p in items:
        html += (
            '<div class="aside-recent-post"><a href="' + p["url"] + '">'
            '<div class="aside-post-cover"><img class="aside-post-bg" src="' + p["cover"] + '" onerror="this.onerror=null;this.src=\'/img/404.jpg\'" title="' + p["title"] + '" alt="' + p["title"] + '"/></div>'
            '<div class="aside-post-title"><div class="aside-post_title" href="' + p["url"] + '" title="' + p["title"] + '">' + p["title"] + '</div>'
            '<time class="aside-post_meta post-meta__date" title="发表于 ' + p["date"] + '">' + p["date"] + '</time></div></a></div>'
        )
    html += '</div></div></div>'
    return html


def build_sidebar_categories(posts):
    cats = category_counts(posts)
    names = sorted(cats, key=lambda c: (-cats[c], c))
    html = '<div class="card-widget card-categories"><div class="card-content">'
    html += '<div class="item-headline"><i class="fa fa-folder-open" aria-hidden="true"></i><span>分类</span></div><ul class="card-category-list">'
    for name in names:
        html += (f'<li class="card-category-list-item"><a class="card-category-list-link" href="/categories/{quote(name, safe="")}/">'
                 f'<span class="card-category-list-name">{name}</span><span class="card-category-list-count">{cats[name]}</span></a></li>')
    html += '</ul></div></div>'
    return html


def build_sidebar_tags(posts):
    tags = tag_counts(posts)
    names = sorted(tags, key=lambda t: (-tags[t], t))
    maxc = max(tags.values()) if tags else 1
    html = '<div class="card-widget card-tags"><div class="card-content">'
    html += '<div class="item-headline"><i class="fa fa-tags" aria-hidden="true"></i><span>标签</span></div><div class="card-tag-cloud">'
    for name in names:
        html += f'<a href=\'/tags/{quote(name, safe="")}/\' style=\'font-size:{_tag_size(tags[name], maxc)}px; color:{_color(name)}\'>{name}</a>'
    html += '</div></div></div>'
    return html


def build_sidebar_archives(posts):
    months = month_counts(posts)
    keys = sorted(months, reverse=True)
    html = '<div class="card-widget card-archives"><div class="card-content">'
    html += '<div class="item-headline"><i class="fa fa-archive" aria-hidden="true"></i><span>归档</span></div><ul class="card-archive-list">'
    for k in keys:
        y, mm = k.split("-")
        html += (f'<li class="card-archive-list-item"><a class="card-archive-list-link" href="/archives/{y}/{mm}/">'
                 f'<span class="card-archive-list-date">{MONTHS_CN[int(mm)-1]} {y}</span>'
                 f'<span class="card-archive-list-count">{months[k]}</span></a></li>')
    html += '</ul></div></div>'
    return html


def update_sidebar_in_html(html, posts):
    """把页面里旧的侧边栏小部件替换为新数据"""
    html = re.sub(r'<div class="card-widget card-recent-post">.*?(?=<div class="card-widget card-categories">)',
                  build_sidebar_recent(posts), html, flags=re.S)
    html = re.sub(r'<div class="card-widget card-categories">.*?(?=<div class="card-widget card-tags">)',
                  build_sidebar_categories(posts), html, flags=re.S)
    html = re.sub(r'<div class="card-widget card-tags">.*?(?=<div class="card-widget card-archives">)',
                  build_sidebar_tags(posts), html, flags=re.S)
    html = re.sub(r'<div class="card-widget card-archives">.*?(?=<div class="card-widget card-webinfo">)',
                  build_sidebar_archives(posts), html, flags=re.S)

    cats = category_counts(posts)
    tags = tag_counts(posts)
    total = len(posts)

    info_data = (f'<div class="card-info-data">'
                 f'<div class="card-info-data-item is-center"><a href="/archives"><div class="headline">文章</div><div class="length_num">{total}</div></a></div>'
                 f'<div class="card-info-data-item is-center"><a href="/tags"><div class="headline">标签</div><div class="length_num">{len(tags)}</div></a></div>'
                 f'<div class="card-info-data-item is-center"><a href="/categories"><div class="headline">分类</div><div class="length_num">{len(cats)}</div></a></div></div>')

    mobile_data = (f'<div class="mobile_post_data">'
                   f'<div class="mobile_data_item is-center"><div class="mobile_data_link"><a href="/archives/"><div class="headline">文章</div><div class="length_num">{total}</div></a></div></div>'
                   f'<div class="mobile_data_item is-center"><div class="mobile_data_link"><a href="/tags/"><div class="headline">标签</div><div class="length_num">{len(tags)}</div></a></div></div>'
                   f'<div class="mobile_data_item is-center"><div class="mobile_data_link"><a href="/categories/"><div class="headline">分类</div><div class="length_num">{len(cats)}</div></a></div></div></div>')

    html = re.sub(r'<div class="card-info-data">.*?(?=<div class="card-info-bookmark)', info_data, html, flags=re.S)
    html = re.sub(r'<div class="mobile_post_data">.*?(?=<hr/>)', mobile_data, html, flags=re.S)
    html = re.sub(r'<div class="webinfo-article-count">.*?</div>', f'<div class="webinfo-article-count">{total}</div>', html, flags=re.S)
    return html


# ============================================================
# 页面重建
# ============================================================
def _read(path):
    return path.read_text(encoding="utf-8")


def _write(path, content):
    path.write_text(content, encoding="utf-8")


def rebuild_index(posts):
    index_path = REPO_ROOT / "index.html"
    template = _read(index_path)
    total_pages = (len(posts) + PER_PAGE - 1) // PER_PAGE

    def page_html(page):
        start = (page - 1) * PER_PAGE
        items = posts[start:start + PER_PAGE]
        cards = ""
        for i, p in enumerate(items):
            cards += build_card(p, "left_radius" if i % 2 == 0 else "right_radius")
        # 注意：分页 <nav> 原本位于 #recent-posts 内部，用 aside_content 作为边界整体替换
        html = re.sub(r'<div class="recent-posts" id="recent-posts">.*?(?=<div class="aside_content")',
                      f'<div class="recent-posts" id="recent-posts">{cards}{build_pagination(page, total_pages)}</div>',
                      template, count=1, flags=re.S)
        return update_sidebar_in_html(html, posts)

    _write(index_path, page_html(1))
    for page in range(2, total_pages + 1):
        out_dir = REPO_ROOT / "page" / str(page)
        out_dir.mkdir(parents=True, exist_ok=True)
        _write(out_dir / "index.html", page_html(page))
    # 清理多余的历史分页
    for page in range(total_pages + 1, 20):
        p = REPO_ROOT / "page" / str(page)
        if p.exists():
            for f in p.glob("index.html"):
                f.unlink()
    print(f"[OK] 首页：{len(posts)} 篇，{total_pages} 页（每页 {PER_PAGE} 篇）")


def rebuild_archives(posts):
    path = REPO_ROOT / "archives" / "index.html"
    html = _read(path)
    # 归档不分页，一次性列出全部（原分页 <nav> 位于 #archive 内部，整体替换掉）
    html = re.sub(r'<div id="archive">.*?(?=<div class="aside_content")',
                  f'<div id="archive">{build_archive(posts)}</div>', html, count=1, flags=re.S)
    _write(path, update_sidebar_in_html(html, posts))
    print(f"[OK] 归档：{len(posts)} 篇")


def rebuild_categories(posts):
    path = REPO_ROOT / "categories" / "index.html"
    html = _read(path)
    html = re.sub(r'<div class="category-lists">.*?</div>(?=<hr>)',
                  f'<div class="category-lists">{build_category_list(posts)}</div>', html, count=1, flags=re.S)
    _write(path, update_sidebar_in_html(html, posts))
    print(f"[OK] 分类：{len(category_counts(posts))} 个")


def rebuild_tags(posts):
    path = REPO_ROOT / "tags" / "index.html"
    html = _read(path)
    html = re.sub(r'<div class="tag-cloud">.*?(?=<hr>)',
                  f'<div class="tag-cloud">{build_tag_cloud(posts)}', html, count=1, flags=re.S)
    _write(path, update_sidebar_in_html(html, posts))
    print(f"[OK] 标签：{len(tag_counts(posts))} 个")


# ============================================================
# 详情页（分类 / 标签 / 归档月份）
# ============================================================
def build_detail_page(template_html, title, canonical, container_id, content_inner):
    html = template_html
    html = re.sub(r"<title>.*?</title>", f"<title>{title} | 小曾博客</title>", html, count=1, flags=re.S)
    html = re.sub(r'<link rel="canonical" href="[^"]*"',
                  f'<link rel="canonical" href="https://xiaozeng26.github.io{canonical}"', html, count=1)
    html = re.sub(r'<div id="category">.*?(?=<div class="aside_content")',
                  f'<div id="{container_id}">{content_inner}</div>', html, count=1, flags=re.S)
    return html


def rebuild_detail_pages(posts):
    template_path = REPO_ROOT / "categories" / "Docker" / "index.html"
    if not template_path.exists():
        print("[WARN] 缺少分类详情页模板，跳过详情页生成")
        return
    template = _read(template_path)

    cats = category_counts(posts)
    tags = tag_counts(posts)
    months = month_counts(posts)

    n = 0
    for name in cats:
        plist = [p for p in posts if p["category"] == name]
        inner = build_archive(plist, f"分类 - {name}")
        out = REPO_ROOT / "categories" / name / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        html = build_detail_page(template, f"分类 - {name}", f"/categories/{quote(name, safe='')}/", "category", inner)
        _write(out, update_sidebar_in_html(html, posts))
        n += 1

    for name in tags:
        plist = [p for p in posts if name in p["tags"]]
        inner = build_archive(plist, f"标签 - {name}")
        out = REPO_ROOT / "tags" / name / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        html = build_detail_page(template, f"标签 - {name}", f"/tags/{quote(name, safe='')}/", "tag", inner)
        _write(out, update_sidebar_in_html(html, posts))
        n += 1

    for key in months:
        y, mm = key.split("-")
        plist = [p for p in posts if p["year"] == int(y) and p["month"] == int(mm)]
        inner = build_archive(plist, f"{y} 年 {int(mm)} 月")
        out = REPO_ROOT / "archives" / y / mm / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        html = build_detail_page(template, f"归档 - {y}年{int(mm)}月", f"/archives/{y}/{mm}/", "archive", inner)
        _write(out, update_sidebar_in_html(html, posts))
        n += 1

    print(f"[OK] 详情页：{len(cats)} 分类 / {len(tags)} 标签 / {len(months)} 月份，共 {n} 页")


# ============================================================
# 公告
# ============================================================
def update_announcement():
    count = 0
    for f in REPO_ROOT.rglob("*.html"):
        if ".git" in f.parts:
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "This is my Blog" not in content:
            continue
        new = content.replace("This is my Blog", ANNOUNCEMENT)
        if new != content:
            f.write_text(new, encoding="utf-8")
            count += 1
    print(f"[OK] 公告：{count} 个文件")


# ============================================================
# 主流程
# ============================================================
def rebuild_after_new_post():
    posts = build_posts_json()
    rebuild_index(posts)
    rebuild_archives(posts)
    rebuild_categories(posts)
    rebuild_tags(posts)
    rebuild_detail_pages(posts)
    return posts


def main():
    data_only = "--data-only" in sys.argv
    print("=" * 60)
    print("  博客静态重建")
    print("=" * 60)

    posts = build_posts_json()
    if data_only:
        print("[DONE] 仅重建 posts.json")
        return

    update_announcement()
    rebuild_index(posts)
    rebuild_archives(posts)
    rebuild_categories(posts)
    rebuild_tags(posts)
    rebuild_detail_pages(posts)
    print("=" * 60)
    print("[DONE] 全部完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
