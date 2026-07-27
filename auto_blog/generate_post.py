#!/usr/bin/env python3
"""
自动博客生成与发布脚本
功能：
  1. 从 config.json 读取话题配置，按权重随机选题
  2. 调用 Claude API 生成高质量技术博客内容
  3. 生成符合现有 Hexo 模板的 HTML 文件
  4. 更新首页 index.html 的文章列表
  5. 更新 atom.xml 和 sitemap.xml
  6. Git commit & push 到 GitHub Pages
"""

import json
import os
import re
import sys
import random
import subprocess
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

# ============================================================
# 配置加载
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
HISTORY_PATH = SCRIPT_DIR / "history.json"

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_history():
    if HISTORY_PATH.exists():
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"generated": [], "used_subtopics": {}}

def save_history(history):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ============================================================
# 去重与选题逻辑
# ============================================================
def _get_recent_categories(history, days=3):
    """获取最近 N 天内使用过的分类"""
    from datetime import datetime as dt, timedelta
    cutoff = (dt.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    recent = set()
    for post in history.get("generated", []):
        if post.get("date", "") >= cutoff:
            recent.add(post.get("category", ""))
    return recent

def _get_all_generated_titles(history):
    """获取所有已生成的文章标题（用于标题相似度检测）"""
    return {post.get("title", "") for post in history.get("generated", [])}

def choose_topic(config, history):
    """
    增强选题逻辑：
    1. 同一分类在 min_days_between_same_category 天内不重复
    2. 子话题用完后才重置该分类
    3. 严格的子话题去重
    """
    topics = config["topics"]
    weights = config["topic_weights"]
    used_subtopics = history.get("used_subtopics", {})
    dedup_cfg = config.get("dedup", {})
    min_days = dedup_cfg.get("min_days_between_same_category", 3)

    # 获取最近 N 天内使用过的分类，避免短期重复
    recent_cats = _get_recent_categories(history, days=min_days)
    all_titles = _get_all_generated_titles(history)

    # 过滤出有权重且存在的 topics
    available = [(k, weights.get(k, 10)) for k in topics.keys()]
    available.sort(key=lambda x: x[1], reverse=True)

    categories = [a[0] for a in available]
    cat_weights = [a[1] for a in available]

    # 优先选择近期没用过的分类
    fresh_cats = [c for c in categories if topics[c].get("category", c) not in recent_cats]

    # 如果所有分类近期都用过了，则不过滤
    target_cats = fresh_cats if fresh_cats else categories
    target_weights = [weights.get(c, 10) for c in target_cats]

    # 按权重加权随机，多轮尝试
    shuffled_cats = random.choices(target_cats, weights=target_weights, k=len(target_cats) * 3)

    for cat in shuffled_cats:
        subtopics = topics[cat]["subtopics"]
        used = used_subtopics.get(cat, [])
        # 过滤掉已使用的子话题（子话题去重）
        available_subs = [s for s in subtopics if s not in used]

        if not available_subs:
            # 该分类所有子话题已用完，重置该分类的使用记录
            used_subtopics[cat] = []
            available_subs = subtopics

        chosen = random.choice(available_subs)
        used_subtopics[cat] = used_subtopics.get(cat, []) + [chosen]
        history["used_subtopics"] = used_subtopics

        # 检查标题是否已生成过（双重保险）
        if chosen in all_titles:
            continue

        return cat, chosen, topics[cat]

    # fallback: 全随机（理论上不会到达这里）
    cat = random.choice(categories)
    chosen = random.choice(topics[cat]["subtopics"])
    return cat, chosen, topics[cat]

# ============================================================
# AI API 调用（支持多Provider: deepseek / claude）
# ============================================================
SYSTEM_PROMPT = "你是一位资深技术博客作者，拥有10年以上Java/Go/Python全栈开发经验，擅长将复杂的技术原理用通俗易懂的方式讲清楚。文章内容要有源码级深度，同时用生活化类比降低理解门槛。"

def _get_api_key(config):
    """获取 API Key，优先级：环境变量 > .apikey 文件"""
    provider = config["generation"].get("api_provider", "deepseek")
    env_key_map = {
        "deepseek": "DEEPSEEK_API_KEY",
        "claude": "ANTHROPIC_API_KEY",
    }
    env_var = env_key_map.get(provider, "DEEPSEEK_API_KEY")

    api_key = os.environ.get(env_var, "")
    if api_key:
        return api_key

    # 从 .apikey 文件读取
    key_file = SCRIPT_DIR / ".apikey"
    if key_file.exists():
        content = key_file.read_text().strip()
        # 如果有等号，取=后面的值
        if "=" in content and not content.startswith("sk-"):
            api_key = content.split("=", 1)[1].strip()
        elif content.startswith("sk-"):
            api_key = content
        if api_key:
            return api_key

    raise RuntimeError(
        f"未找到 {env_var}！请设置环境变量或在 auto_blog/.apikey 文件中写入 API Key\n"
        f"获取 DeepSeek API Key: https://platform.deepseek.com/api_keys\n"
        f"获取 Claude API Key: https://console.anthropic.com/settings/keys"
    )

def call_deepseek_api(prompt, config):
    """调用 DeepSeek API（兼容 OpenAI 格式）"""
    import urllib.request
    import urllib.error

    api_key = _get_api_key(config)
    gen_cfg = config["generation"]
    model = gen_cfg.get("model", "deepseek-chat")

    body = json.dumps({
        "model": model,
        "max_tokens": gen_cfg.get("max_tokens", 8000),
        "temperature": gen_cfg.get("temperature", 0.8),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]
    })

    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
    )

    for attempt in range(gen_cfg.get("retry_count", 3)):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            if attempt < gen_cfg.get("retry_count", 3) - 1:
                print(f"[重试] API 请求失败（第{attempt+1}次）: {e.code}，等待重试...")
                import time
                time.sleep((attempt + 1) * 5)
            else:
                raise RuntimeError(f"DeepSeek API 请求失败: {e.code} - {error_body}")
        except Exception as e:
            if attempt < gen_cfg.get("retry_count", 3) - 1:
                print(f"[重试] 网络错误（第{attempt+1}次）: {e}，等待重试...")
                import time
                time.sleep((attempt + 1) * 5)
            else:
                raise RuntimeError(f"DeepSeek API 网络错误: {e}")

def call_claude_api(prompt, config):
    """调用 Anthropic Claude API"""
    import urllib.request
    import urllib.error

    api_key = _get_api_key(config)
    gen_cfg = config["generation"]
    model = gen_cfg.get("model", "claude-sonnet-4-20250514")

    body = json.dumps({
        "model": model,
        "max_tokens": gen_cfg.get("max_tokens", 8000),
        "temperature": gen_cfg.get("temperature", 0.8),
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": prompt}]
    })

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01"
        }
    )

    for attempt in range(gen_cfg.get("retry_count", 3)):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                for content_item in result.get("content", []):
                    if content_item.get("type") == "text":
                        return content_item["text"]
                raise RuntimeError(f"Claude API 返回格式异常: {result}")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            if attempt < gen_cfg.get("retry_count", 3) - 1:
                print(f"[重试] API 请求失败（第{attempt+1}次）: {e.code}，等待重试...")
                import time
                time.sleep((attempt + 1) * 5)
            else:
                raise RuntimeError(f"Claude API 请求失败: {e.code} - {error_body}")
        except Exception as e:
            if attempt < gen_cfg.get("retry_count", 3) - 1:
                print(f"[重试] 网络错误（第{attempt+1}次）: {e}，等待重试...")
                import time
                time.sleep((attempt + 1) * 5)
            else:
                raise RuntimeError(f"Claude API 网络错误: {e}")

def call_ai_api(prompt, config):
    """统一的 AI API 调用入口，根据配置选择 provider"""
    provider = config["generation"].get("api_provider", "deepseek")
    print(f"[API] Provider: {provider}, Model: {config['generation'].get('model', 'default')}")

    if provider == "claude":
        return call_claude_api(prompt, config)
    else:
        # 默认使用 DeepSeek
        return call_deepseek_api(prompt, config)

# ============================================================
# Prompt 构建
# ============================================================
def build_prompt(category, subtopic, topic_config, config):
    """构建生成 prompt"""
    cfg = config["content_settings"]
    blog = config["blog"]
    date_str = datetime.now().strftime("%Y年%m月%d日")

    prompt = f"""请以一位资深全栈架构师的身份，撰写一篇高质量中文技术博客。

博客标题：{subtopic}
所属分类：{topic_config['category']}
标签：{', '.join(topic_config['tags'])}
发布日期：{date_str}
博客名称：{blog['title']}

## 内容要求

### 深度要求
- 你面向的是有3-5年经验的开发工程师，内容要有真正的高级深度，不要写入门级内容
- 必须包含源码级别的分析（如果有相关源码）
- 必须包含至少 3 个代码示例，代码要完整可运行
- 涉及到架构设计的地方，请用文字描述流程图或时序图的结构（我会转为mermaid图）

### 风格要求
- 用生活化的类比来解释复杂原理（比如用餐厅后厨类比线程池，用快递分拣类比消息队列）
- 对比不同的实现方案，分析各自的优缺点和适用场景
- 给出最佳实践和常见的坑

### 文章结构
1. **引言**：用实际场景引出问题（200字左右）
2. **核心概念**：生活类比 + 技术定义（400字左右）
3. **源码/原理深度分析**：这是核心部分，需要足够的深度（1500-2000字）
4. **实战代码**：至少3个完整的可运行代码示例（包含详细注释）
5. **方案对比**：对比业界其他方案或相关技术的异同（400字左右）
6. **最佳实践与避坑指南**：给出工程实践建议（300字左右）
7. **总结**：回顾要点和延伸思考（200字左右）

### Mermaid 图表
在适当的位置插入 mermaid 图表占位符（用 ```mermaid 代码块），至少包含1个流程图或架构图或时序图。
例如：
```mermaid
graph TD
    A[客户端请求] --> B[API网关]
    B --> C[服务A]
    C --> D[数据库]
```

### 输出格式
请直接用 Markdown 格式输出完整的博客文章。不要包含任何前言或后记。
文章开头直接从 # 标题 开始即可。"""

    return prompt

# ============================================================
# HTML 生成
# ============================================================
def build_post_html(title, category, tags, date_str, content_html, cover_img=None):
    """生成匹配现有 Hexo 模板的完整 HTML 页面"""
    blog_url = "https://xiaozeng26.github.io"
    blog_title = "小曾博客"
    author = "小曾"
    description = content_html[:200].replace('"', '\\"').replace('\n', ' ')

    if not cover_img:
        # 随机选一张封面图
        img_num = random.randint(1000, 5000)
        cover_img = f"/img/{img_num}.jpg"

    # URL 编码
    encoded_title = quote(title, safe='')

    # 日期相关
    now = datetime.now(timezone(timedelta(hours=8)))
    date_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    date_display = now.strftime("%Y-%m-%d")
    date_html5 = now.strftime("%Y-%m-%d")

    html = f'''<!DOCTYPE html>
<html lang="zh-CN" data-theme="light">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title} | {blog_title}</title>
  <meta name="description" content="{description}">
  <meta name="keywords" content="{','.join(tags)}">
  <meta name="author" content="{author}">
  <meta name="copyright" content="{author}">
  <meta name="format-detection" content="telephone=no">
  <link rel="shortcut icon" href="/img/favicon.png">
  <meta http-equiv="Cache-Control" content="no-transform">
  <meta http-equiv="Cache-Control" content="no-siteapp">
  <link rel="preconnect" href="//cdn.jsdelivr.net"/>
  <link rel="dns-prefetch" href="//cdn.jsdelivr.net"/>
  <link rel="preconnect" href="https://fonts.googleapis.com" crossorigin="crossorigin"/>
  <link rel="dns-prefetch" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="//busuanzi.ibruce.info"/>
  <link rel="dns-prefetch" href="//busuanzi.ibruce.info"/>
  <meta name="twitter:card" content="summary">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{description}">
  <meta name="twitter:image" content="{blog_url}{cover_img}">
  <meta property="og:type" content="article">
  <meta property="og:title" content="{title}">
  <meta property="og:url" content="{blog_url}/">
  <meta property="og:site_name" content="{blog_title}">
  <meta property="og:description" content="{description}">
  <meta property="og:image" content="{blog_url}{cover_img}">
  <meta property="article:published_time" content="{date_iso}">
  <meta property="article:modified_time" content="{date_iso}">
  <script src="https://cdn.jsdelivr.net/npm/js-cookie/dist/js.cookie.min.js"></script>
  <script>
var autoChangeMode = '1'
var t = Cookies.get("theme")
if (autoChangeMode == '1'){{
  var isDarkMode = window.matchMedia("(prefers-color-scheme: dark)").matches
  var isLightMode = window.matchMedia("(prefers-color-scheme: light)").matches
  var isNotSpecified = window.matchMedia("(prefers-color-scheme: no-preference)").matches
  var hasNoSupport = !isDarkMode && !isLightMode && !isNotSpecified
  if (t === undefined){{
    if (isLightMode) activateLightMode()
    else if (isDarkMode) activateDarkMode()
    else if (isNotSpecified || hasNoSupport){{
      console.log('You specified no preference for a color scheme or your browser does not support it. I Schedule dark mode during night time.')
      var now = new Date()
      var hour = now.getHours()
      var isNight = hour < 6 || hour >= 18
      isNight ? activateDarkMode() : activateLightMode()
  }}
  }} else if (t == 'light') activateLightMode()
  else activateDarkMode()
}} else if (autoChangeMode == '2'){{
  now = new Date();
  hour = now.getHours();
  isNight = hour < 6 || hour >= 18
  if(t === undefined) isNight? activateDarkMode() : activateLightMode()
  else if (t === 'light') activateLightMode()
  else activateDarkMode()
}} else {{
  if ( t == 'dark' ) activateDarkMode()
  else if ( t == 'light') activateLightMode()
}}
function activateDarkMode(){{
  document.documentElement.setAttribute('data-theme', 'dark')
  if (document.querySelector('meta[name="theme-color"]') !== null){{
    document.querySelector('meta[name="theme-color"]').setAttribute('content','#000')
  }}
}}
function activateLightMode(){{
  document.documentElement.setAttribute('data-theme', 'light')
  if (document.querySelector('meta[name="theme-color"]') !== null){{
  document.querySelector('meta[name="theme-color"]').setAttribute('content','#fff')
  }}
}}
  </script>
  <link rel="stylesheet" href="/css/index.css">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/font-awesome@latest/css/font-awesome.min.css">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@fancyapps/fancybox@latest/dist/jquery.fancybox.min.css">
  <link rel="canonical" href="{blog_url}/">
  <link rel="stylesheet" type="text/css" href="https://cdn.jsdelivr.net/npm/disqusjs@1.2/dist/disqusjs.css">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css?family=Titillium+Web">
  <script>
var GLOBAL_CONFIG = {{
  root: '/',
  algolia: undefined,
  localSearch: undefined,
  translate: {{"defaultEncoding":2,"translateDelay":0,"cookieDomain":"{blog_url}/","msgToTraditionalChinese":"繁","msgToSimplifiedChinese":"簡"}},
  copy: {{ success: '复制成功', error: '复制错误', noSupport: '浏览器不支持' }},
  bookmark: {{ message_prev: '按', message_next: '键将本页加入书签' }},
  runtime_unit: '天',
  runtime: true,
  copyright: undefined,
  ClickShowText: undefined,
  medium_zoom: false,
  fancybox: true,
  Snackbar: undefined,
  baiduPush: false,
  highlightCopy: true,
  highlightLang: true,
  highlightShrink: 'false',
  isFontAwesomeV5: false,
  isPhotoFigcaption: true,
  islazyload: false,
  isanchor: true
}}
  </script>
  <script>var GLOBAL_CONFIG_SITE = {{ isPost: true, isHome: false, isSidebar: true }}</script>
  <noscript><style>#page-header {{ opacity: 1 }} .justified-gallery img{{ opacity: 1 }}</style></noscript>
  <link rel="stylesheet" href="/css/background.css">
  <meta name="generator" content="Hexo 4.2.0">
  <link rel="alternate" href="/atom.xml" title="{blog_title}" type="application/atom+xml">
</head>
<body>
<div id="mobile-sidebar">
  <div id="menu_mask"></div>
  <div id="mobile-sidebar-menus">
    <div class="mobile_author_icon"><img class="avatar-img" src="/img/avatar.jfif" onerror="onerror=null;src='/img/friend_404.gif'" alt="avatar"/></div>
    <div class="mobile_post_data">
      <div class="mobile_data_item is-center"><div class="mobile_data_link"><a href="/archives/"><div class="headline">文章</div></a></div></div>
      <div class="mobile_data_item is-center"><div class="mobile_data_link"><a href="/tags/"><div class="headline">标签</div></a></div></div>
      <div class="mobile_data_item is-center"><div class="mobile_data_link"><a href="/categories/"><div class="headline">分类</div></a></div></div>
    </div>
    <hr/>
    <div class="menus_items">
      <div class="menus_item"><a class="site-page" href="/"><i class="fa-fw fa fa-home"></i><span> 首页</span></a></div>
      <div class="menus_item"><a class="site-page" href="/archives/"><i class="fa-fw fa fa-archive"></i><span> 时间轴</span></a></div>
      <div class="menus_item"><a class="site-page" href="/tags/"><i class="fa-fw fa fa-tags"></i><span> 标签</span></a></div>
      <div class="menus_item"><a class="site-page" href="/categories/"><i class="fa-fw fa fa-folder-open"></i><span> 分类</span></a></div>
      <div class="menus_item"><a class="site-page" href="/link/"><i class="fa-fw fa fa-link"></i><span> 友链</span></a></div>
      <div class="menus_item"><a class="site-page" href="/messageboard/"><i class="fa-fw fa fa-link"></i><span> 留言板</span></a></div>
      <div class="menus_item"><a class="site-page" href="/about/"><i class="fa-fw fa fa-heart"></i><span> 关于我</span></a></div>
      <div class="menus_item">
        <a class="site-page"><i class="fa-fw fa fa-list" aria-hidden="true"></i><span> 娱乐</span><i class="fa fa-chevron-down menus-expand" aria-hidden="true"></i></a>
        <ul class="menus_item_child">
          <li><a class="site-page" href="/music/"><i class="fa-fw fa fa-music"></i><span> Music</span></a></li>
          <li><a class="site-page" href="/photos/"><i class="fa-fw fa fa-picture-o"></i><span> Photos</span></a></li>
          <li><a class="site-page" href="/movies/"><i class="fa-fw fa fa-film"></i><span> Movie</span></a></li>
        </ul>
      </div>
    </div>
  </div>
</div>
<i class="fa fa-arrow-right on" id="toggle-sidebar" aria-hidden="true"></i>
<div id="sidebar">
  <div class="sidebar-toc">
    <div class="sidebar-toc__title">目录</div>
    <div class="sidebar-toc__progress"><span class="progress-notice">你已经读了</span><span class="progress-num">0</span><span class="progress-percentage">%</span><div class="sidebar-toc__progress-bar"></div></div>
    <div class="sidebar-toc__content">{generate_toc(content_html)}</div>
  </div>
</div>
<div id="body-wrap">
  <div class="post-bg" id="nav" style="background-image: url({cover_img})">
    <div id="page-header">
      <span class="pull_left" id="blog_name"><a class="blog_title" id="site-name" href="/">{blog_title}</a></span>
      <span class="pull_right menus">
        <div class="menus_items">
          <div class="menus_item"><a class="site-page" href="/"><i class="fa-fw fa fa-home"></i><span> 首页</span></a></div>
          <div class="menus_item"><a class="site-page" href="/archives/"><i class="fa-fw fa fa-archive"></i><span> 时间轴</span></a></div>
          <div class="menus_item"><a class="site-page" href="/tags/"><i class="fa-fw fa fa-tags"></i><span> 标签</span></a></div>
          <div class="menus_item"><a class="site-page" href="/categories/"><i class="fa-fw fa fa-folder-open"></i><span> 分类</span></a></div>
          <div class="menus_item"><a class="site-page" href="/link/"><i class="fa-fw fa fa-link"></i><span> 友链</span></a></div>
          <div class="menus_item"><a class="site-page" href="/messageboard/"><i class="fa-fw fa fa-link"></i><span> 留言板</span></a></div>
          <div class="menus_item"><a class="site-page" href="/about/"><i class="fa-fw fa fa-heart"></i><span> 关于我</span></a></div>
          <div class="menus_item">
            <a class="site-page"><i class="fa-fw fa fa-list" aria-hidden="true"></i><span> 娱乐</span><i class="fa fa-chevron-down menus-expand" aria-hidden="true"></i></a>
            <ul class="menus_item_child">
              <li><a class="site-page" href="/music/"><i class="fa-fw fa fa-music"></i><span> Music</span></a></li>
              <li><a class="site-page" href="/photos/"><i class="fa-fw fa fa-picture-o"></i><span> Photos</span></a></li>
              <li><a class="site-page" href="/movies/"><i class="fa-fw fa fa-film"></i><span> Movie</span></a></li>
            </ul>
          </div>
        </div>
        <span class="toggle-menu close"><a class="site-page"><i class="fa fa-bars fa-fw" aria-hidden="true"></i></a></span>
      </span>
    </div>
    <div id="post-info">
      <div id="post-title"><div class="posttitle">{title}</div></div>
      <div id="post-meta">
        <div class="meta-firstline">
          <time class="post-meta__date">
            <span class="post-meta__date-created" title="发表于 {date_display}"><i class="fa fa-calendar" aria-hidden="true"></i> 发表于 {date_display}</span>
            <span class="post-meta__separator">|</span>
            <span class="post-meta__date-updated" title="更新于 {date_display}"><i class="fa fa-history" aria-hidden="true"></i> 更新于 {date_display}</span>
          </time>
        </div>
        <div class="meta-secondline">
          <span class="post-meta-categories">
            <span class="post-meta__separator">|</span>
            <i class="fa fa-inbox post-meta__icon" aria-hidden="true"></i>
            <a class="post-meta__categories" href="/categories/">{category}</a>
          </span>
        </div>
        <div class="meta-thirdline">
          <span class="post-meta-pv-cv"><i class="fa fa-eye post-meta__icon" aria-hidden="true"></i><span>阅读量:</span><span id="busuanzi_value_page_pv"></span></span>
          <span class="post-meta-commentcount"></span>
        </div>
      </div>
    </div>
  </div>
  <main class="layout_post" id="content-inner">
    <article id="post">
      <div class="post-content" id="article-container">
        {content_html}
      </div>
    </article>
  </main>
</div>
<footer id="footer">
  <div id="footer-wrap">
    <div class="copyright">&copy;2020 - {now.year} By {author}</div>
    <div class="framework-info"><span>框架 </span><a target="_blank" rel="noopener" href="https://hexo.io">Hexo</a><span class="footer-separator">|</span><span>主题 </span><a target="_blank" rel="noopener" href="https://github.com/jerryc127/hexo-theme-butterfly">Butterfly</a></div>
  </div>
</footer>
<script src="/js/utils.js"></script>
<script src="/js/main.js"></script>
<script src="https://cdn.jsdelivr.net/npm/instant.page@latest" type="module"></script>
</body>
</html>'''

    return html

def generate_toc(content_html):
    """从 HTML 内容生成简易目录"""
    # 匹配 h1-h3 标题
    headings = re.findall(r'<h([1-3])\s+id="([^"]*)"[^>]*>(.*?)</h\1>', content_html)
    if not headings:
        return '<div class="sidebar-toc__content"><ol class="toc"></ol></div>'

    toc_items = []
    for level, anchor, text in headings:
        clean_text = re.sub(r'<[^>]+>', '', text)
        toc_items.append(f'<li class="toc-item toc-level-{level}"><a class="toc-link" href="#{anchor}"><span class="toc-text">{clean_text}</span></a></li>')

    toc_html = '<div class="sidebar-toc__content"><ol class="toc">' + '\n'.join(toc_items) + '</ol></div>'
    return toc_html

# ============================================================
# Markdown → HTML 转换
# ============================================================
def markdown_to_html(md_text):
    """简单的 Markdown 转 HTML（专注于代码块、mermaid 和高亮）"""
    import html as html_module

    lines = md_text.split('\n')
    result = []
    i = 0
    in_code_block = False
    code_lang = ""
    code_content = []
    in_mermaid = False
    mermaid_content = []

    while i < len(lines):
        line = lines[i]

        # Mermaid 代码块
        if line.strip().startswith('```mermaid'):
            in_mermaid = True
            mermaid_content = []
            i += 1
            continue

        if in_mermaid and line.strip() == '```':
            in_mermaid = False
            mermaid_div = f'<div class="mermaid">{chr(10).join(mermaid_content)}</div>'
            result.append(mermaid_div)
            i += 1
            continue

        if in_mermaid:
            mermaid_content.append(line)
            i += 1
            continue

        # 普通代码块
        if line.strip().startswith('```'):
            if not in_code_block:
                in_code_block = True
                code_lang = line.strip()[3:].strip()
                code_content = []
            else:
                in_code_block = False
                lang_class = f' class="language-{code_lang}"' if code_lang else ''
                escaped = html_module.escape('\n'.join(code_content))
                result.append(f'<figure class="highlight {code_lang}"><pre><code{lang_class}>{escaped}</code></pre></figure>')
            i += 1
            continue

        if in_code_block:
            code_content.append(line)
            i += 1
            continue

        # 标题
        if line.startswith('### '):
            text = line[4:]
            anchor = re.sub(r'[^a-zA-Z0-9一-鿿]+', '-', text).strip('-')
            result.append(f'<h3 id="{anchor}"><a href="#{anchor}" class="headerlink" title="{text}"></a>{text}</h3>')
        elif line.startswith('## '):
            text = line[3:]
            anchor = re.sub(r'[^a-zA-Z0-9一-鿿]+', '-', text).strip('-')
            result.append(f'<h2 id="{anchor}"><a href="#{anchor}" class="headerlink" title="{text}"></a>{text}</h2>')
        elif line.startswith('# '):
            text = line[2:]
            anchor = re.sub(r'[^a-zA-Z0-9一-鿿]+', '-', text).strip('-')
            result.append(f'<h1 id="{anchor}"><a href="#{anchor}" class="headerlink" title="{text}"></a>{text}</h1>')

        # 无序列表
        elif line.strip().startswith('- ') or line.strip().startswith('* '):
            text = line.strip()[2:]
            # 处理粗体和代码
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
            text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
            result.append(f'<ul>\n<li>{text}</li>\n</ul>')

        # 有序列表
        elif re.match(r'^\d+\.\s', line.strip()):
            text = re.sub(r'^\d+\.\s', '', line.strip())
            text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
            text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
            text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
            result.append(f'<ol>\n<li>{text}</li>\n</ol>')

        # 普通段落
        elif line.strip():
            processed = line
            processed = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', processed)
            processed = re.sub(r'`([^`]+)`', r'<code>\1</code>', processed)
            processed = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1">', processed)
            processed = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', processed)
            if not processed.startswith('<'):
                result.append(f'<p>{processed}</p>')
            else:
                result.append(processed)
        else:
            result.append('<br>')

        i += 1

    return '\n'.join(result)

# ============================================================
# 首页更新
# ============================================================
def update_index_html(post_title, post_url_path, date_str, content_preview, category, cover_img=None):
    """在 index.html 的文章列表最前面插入新文章"""
    index_path = REPO_ROOT / "index.html"

    if not index_path.exists():
        print("[WARN] index.html 不存在，跳过首页更新")
        return

    content = index_path.read_text(encoding="utf-8")

    img_src = cover_img or f"/img/{random.randint(1000, 5000)}.jpg"
    preview_text = content_preview[:200].replace('"', '\\"').replace('\n', ' ')

    # 构建新文章卡片 HTML
    new_card = f'''<div class="recent-post-item"><div class="post_cover left_radius"><a href="{post_url_path}" title="{post_title}">     <img class="post_bg" src="{img_src}" onerror="this.onerror=null;this.src='/img/404.jpg'" alt="{post_title}"></a></div><div class="recent-post-info"><a class="article-title" href="{post_url_path}" title="{post_title}">{post_title}</a><div class="article-meta-wrap"><time class="post-meta__date"><span class="post-meta__date-created" title="发表于 {date_str}"><i class="fa fa-calendar" aria-hidden="true"></i>{date_str}</span><span class="article-meta__separator">|</span><span class="post-meta__date-updated" title="更新于 {date_str}"><i class="fa fa-history" aria-hidden="true"></i>{date_str}</span></time><span class="article-meta"><span class="article-meta__separator">|</span><i class="fa fa-inbox article-meta__icon" aria-hidden="true"></i><a class="article-meta__categories" href="/categories/">{category}</a></span></div><div class="content">{preview_text}</div></div></div>'''

    # 在第一个 recent-post-item 之前插入
    first_item_pos = content.find('<div class="recent-post-item">')
    if first_item_pos == -1:
        # 找到 recent-posts 容器
        container_pos = content.find('<div class="recent-posts" id="recent-posts">')
        if container_pos != -1:
            insert_pos = content.find('>', container_pos) + 1
        else:
            print("[WARN] 找不到文章列表位置，跳过首页更新")
            return
    else:
        insert_pos = first_item_pos

    new_content = content[:insert_pos] + new_card + content[insert_pos:]
    index_path.write_text(new_content, encoding="utf-8")
    print("[OK] 首页 index.html 已更新")

# ============================================================
# 文件写入
# ============================================================
def write_post_files(title, html_content):
    """创建文章目录并写入 HTML 文件"""
    now = datetime.now(timezone(timedelta(hours=8)))
    year = now.strftime("%Y")
    month = now.strftime("%m")
    day = now.strftime("%d")

    # URL 安全的目录名
    slug = title.replace('/', '-').replace('\\', '-').replace('?', '').replace(':', ' -')
    # URL encode for Chinese characters
    encoded_slug = quote(slug, safe='')

    post_dir = REPO_ROOT / year / month / day / encoded_slug
    post_dir.mkdir(parents=True, exist_ok=True)

    index_file = post_dir / "index.html"
    index_file.write_text(html_content, encoding="utf-8")

    print(f"[OK] 文章已生成: {post_dir}/index.html")
    return f"/{year}/{month}/{day}/{encoded_slug}/"

# ============================================================
# Git 操作
# ============================================================
def git_commit_and_push(post_title):
    """提交并推送到 GitHub"""
    os.chdir(str(REPO_ROOT))

    # 检查 git 状态
    result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if not result.stdout.strip():
        print("[INFO] 没有文件变更，跳过提交")
        return False

    # Git add
    subprocess.run(["git", "add", "."], check=True)
    print("[OK] git add 完成")

    # Git commit
    commit_msg = f"docs: 自动生成博客 - {post_title}"
    subprocess.run(["git", "commit", "-m", commit_msg], check=True)
    print(f"[OK] git commit: {commit_msg}")

    # Git push
    subprocess.run(["git", "push", "origin", "master"], check=True)
    print("[OK] git push 完成")

    return True

# ============================================================
# 主流程
# ============================================================
def main():
    import argparse

    parser = argparse.ArgumentParser(description="自动博客生成与发布")
    parser.add_argument("--no-git", action="store_true",
                        help="跳过 git 提交和推送（GitHub Actions 中由 workflow 统一处理）")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅选题和生成内容，不写入文件也不提交")
    args = parser.parse_args()

    print("=" * 60)
    print("  自动博客生成系统")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.no_git:
        print("  模式: 跳过 Git 操作")
    if args.dry_run:
        print("  模式: Dry Run（仅预览）")
    print("=" * 60)

    # 1. 加载配置和历史
    config = load_config()
    history = load_history()

    # 2. 选题（带去重检查）
    category_key, subtopic, topic_config = choose_topic(config, history)
    category = topic_config['category']
    print(f"\n[选题] [{category}] {subtopic}")

    # 检查该子话题是否已有内容哈希记录（防重复生成完全相同的文章）
    content_hashes = history.get("content_hashes", {})
    if subtopic in content_hashes:
        print(f"[跳过] 该子话题已生成过（hash: {content_hashes[subtopic][:12]}...），跳过")
        return

    # 3. 构建 prompt
    prompt = build_prompt(category_key, subtopic, topic_config, config)
    print(f"[Prompt] 长度: {len(prompt)} 字符")

    # 4. 调用 AI 生成内容
    provider = config["generation"].get("api_provider", "deepseek")
    print(f"\n[生成] 正在调用 {provider.upper()} API 生成博客内容...")
    try:
        md_content = call_ai_api(prompt, config)
        print(f"[生成] 内容长度: {len(md_content)} 字符")
    except RuntimeError as e:
        print(f"\n[ERROR] {e}")
        print("\n请确保已设置 API Key：")
        print("  DeepSeek: https://platform.deepseek.com/api_keys")
        print("  Claude:   https://console.anthropic.com/settings/keys")
        print("配置方法:")
        print("  GitHub Actions: Settings → Secrets → Actions → 添加 DEEPSEEK_API_KEY")
        print("  本地: 在 auto_blog/.apikey 文件中写入 API Key")
        sys.exit(1)

    # 5. 提取标题
    title_match = re.search(r'^#\s+(.+)$', md_content, re.MULTILINE)
    if title_match:
        post_title = title_match.group(1).strip()
    else:
        post_title = subtopic

    print(f"[标题] {post_title}")

    # 6. 内容哈希去重
    content_hash = hashlib.sha256(md_content.encode("utf-8")).hexdigest()
    if content_hash in content_hashes.values():
        print(f"[跳过] 生成的内容与已有文章重复（hash: {content_hash[:12]}...），跳过")
        return
    content_hashes[subtopic] = content_hash
    history["content_hashes"] = content_hashes

    # 7. 检查标题是否已存在
    all_titles = {p.get("title", "") for p in history.get("generated", [])}
    if post_title in all_titles:
        print(f"[跳过] 文章标题 '{post_title}' 已存在")
        return

    if args.dry_run:
        print(f"\n[Dry Run] 文章标题: {post_title}")
        print(f"[Dry Run] 分类: {category}")
        print(f"[Dry Run] 内容预览: {md_content[:300]}...")
        return

    # 8. Markdown → HTML
    content_html = markdown_to_html(md_content)
    date_str = datetime.now().strftime("%Y-%m-%d")

    # 9. 生成完整的 HTML 页面
    full_html = build_post_html(
        title=post_title,
        category=category,
        tags=topic_config['tags'],
        date_str=date_str,
        content_html=content_html
    )

    # 10. 写入文件
    url_path = write_post_files(post_title, full_html)

    # 11. 提取摘要
    plain_text = re.sub(r'<[^>]+>', '', content_html)
    plain_text = re.sub(r'\s+', ' ', plain_text).strip()

    # 12. 更新首页
    update_index_html(
        post_title=post_title,
        post_url_path=url_path,
        date_str=date_str,
        content_preview=plain_text,
        category=category
    )

    # 13. 保存历史
    history["generated"].append({
        "title": post_title,
        "category": category,
        "subtopic": subtopic,
        "date": date_str,
        "url": url_path
    })
    save_history(history)

    # 14. Git 操作
    if args.no_git:
        print("\n[Git] --no-git 模式，跳过提交推送")
        print(f"[SUCCESS] 博客文件已生成，等待外部 Git 操作")
        print(f"  标题: {post_title}")
        print(f"  路径: {url_path}")
    else:
        print("\n[Git] 准备提交到 GitHub...")
        success = git_commit_and_push(post_title)

        if success:
            print(f"\n{'=' * 60}")
            print(f"  [SUCCESS] 博客发布成功!")
            print(f"  标题: {post_title}")
            print(f"  分类: {category}")
            print(f"  访问: https://xiaozeng26.github.io{url_path}")
            print(f"{'=' * 60}")
        else:
            print("\n[INFO] 没有变更需要提交")

if __name__ == "__main__":
    main()
