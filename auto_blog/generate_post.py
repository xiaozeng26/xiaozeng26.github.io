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
    return {"generated": [], "used_subtopics": {}, "content_hashes": {}}

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
        content = key_file.read_text(encoding="utf-8").strip()
        # 如果有等号，取=后面的值
        if "=" in content and not content.startswith("sk-"):
            api_key = content.split("=", 1)[1].strip()
        elif content.startswith("sk-"):
            api_key = content
        if api_key and "your-deepseek-key-here" not in api_key:
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
# AI 输出后处理（切除元信息引用块）
# ============================================================
def _clean_ai_output(md_text):
    """清理 AI 可能输出的元信息引用块和重复标题"""
    lines = md_text.split('\n')
    cleaned = []
    skip_until_empty = False
    seen_h1 = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # 跳过开头的元信息引用块: > 发布... > 分类... > 标签...
        if i < 10 and stripped.startswith('> ') and any(
            kw in stripped for kw in ['发布', '分类', '标签', '日期', '博客', '发表于', '发布于']
        ):
            continue

        # 跳过残留的分类标签行（非 markdown 格式）
        if i < 10 and any(
            stripped.startswith(kw) for kw in ['发布日期', '分类：', '标签：', '博客名称', '所属分类']
        ):
            continue

        # 跳过开头的连续空行（在真正的 # 标题之前）
        if not seen_h1 and not stripped:
            continue

        # 记录已见到第一个 # 标题
        if stripped.startswith('# ') and not seen_h1:
            seen_h1 = True

        cleaned.append(line)

    result = '\n'.join(cleaned).strip()

    # 如果清理后内容为空，返回原始内容
    if len(result) < 100:
        return md_text

    return result

# ============================================================
# Prompt 构建
# ============================================================
def build_prompt(category, subtopic, topic_config, config):
    """构建生成 prompt"""
    cfg = config["content_settings"]
    blog = config["blog"]
    date_str = datetime.now().strftime("%Y年%m月%d日")

    prompt = f"""请以一位资深全栈架构师的身份，撰写一篇高质量中文技术博客。

## 文章信息（仅供你参考，不要在正文中重复输出！）
- 博客标题：{subtopic}
- 所属分类：{topic_config['category']}
- 标签：{', '.join(topic_config['tags'])}
- 发布日期：{date_str}

## 内容要求

### 深度要求
- 面向有3-5年经验的开发工程师，内容要有真正的高级深度，不要写入门级内容
- 必须包含源码级别的分析（如果有相关源码）
- 必须包含至少 3 个完整的可运行代码示例（带详细注释）
- 涉及架构设计的地方，请用 mermaid 代码块画图

### 风格要求
- 用生活化的类比解释复杂原理（如：餐厅后厨类比线程池，快递分拣类比消息队列）
- 对比不同实现方案，分析优缺点和适用场景
- 给出最佳实践和常见坑

### 文章结构
1. **引言**：用实际场景引出问题
2. **核心概念**：生活类比 + 技术定义
3. **源码/原理深度分析**：核心部分，需要足够深度
4. **实战代码**：至少3个完整可运行代码示例
5. **方案对比**：对比业界其他方案的异同
6. **最佳实践与避坑指南**
7. **总结**：回顾要点和延伸思考

### Mermaid 图表（至少1个）
```mermaid
graph TD
    A[客户端] --> B[服务端]
```

## 输出格式（严格遵守！）
1. 第一行必须是 "# 标题" 格式的 Markdown 标题
2. **绝对不要**在正文中输出"发布日期"、"分类"、"标签"、"博客名称"等元信息
3. **绝对不要**用 blockquote（>）包裹任何元信息
4. **绝对不要**输出"发表于"、"发布于"、"分类："、"标签："等内容
5. 直接从 # 标题 开始，然后是 ## 引言，依次展开
6. 除了文章标题（一个 #），不要重复输出文章信息"""

    return prompt

# ============================================================
# HTML 生成
# ============================================================
def build_post_html(title, category, tags, date_str, content_html, cover_img=None):
    """生成匹配现有 Hexo 模板的完整 HTML 页面"""
    blog_url = "https://xiaozeng26.github.io"
    blog_title = "小曾博客"
    author = "小曾"

    # 去除 HTML 标签生成纯文本描述
    plain_desc = re.sub(r'<[^>]+>', '', content_html)
    plain_desc = re.sub(r'\s+', ' ', plain_desc).strip()[:200]
    description = plain_desc.replace('"', '\\"')

    if not cover_img:
        cover_img = get_cover_image(title)

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
  <meta name="twitter:image" content="{cover_img}">
  <meta property="og:type" content="article">
  <meta property="og:title" content="{title}">
  <meta property="og:url" content="{blog_url}/">
  <meta property="og:site_name" content="{blog_title}">
  <meta property="og:description" content="{description}">
  <meta property="og:image" content="{cover_img}">
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
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true, theme:'default', securityLevel:'loose'}});</script>
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
# 封面图片（LoremFlickr 免费图库，按标题种子生成，每篇不同；国内可直连）
# ============================================================
def get_cover_image(title):
    """根据文章标题生成唯一的封面图 URL，同一标题始终返回同一张图"""
    seed = int(hashlib.md5(title.encode("utf-8")).hexdigest(), 16) % 10000
    return f"https://loremflickr.com/800/400?lock={seed}"

# ============================================================
# Markdown → HTML 转换（改进版：正确处理列表组、引用块、段落）
# ============================================================
def markdown_to_html(md_text):
    """将 Markdown 转为 HTML，匹配 Hexo/Butterfly 主题的预期格式"""
    import html as html_module

    lines = md_text.split('\n')
    result = []
    i = 0

    def _inline(text):
        """处理行内格式：粗体、代码、链接、图片"""
        text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
        text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
        text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1">', text)
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2" target="_blank">\1</a>', text)
        return text

    def _anchor(text):
        """生成标题的锚点 ID"""
        a = re.sub(r'<[^>]+>', '', text)
        a = re.sub(r'[^a-zA-Z0-9一-鿿]+', '-', a).strip('-')
        return a or 'heading'

    def _flush_block():
        """将缓存的同类行合并输出"""
        nonlocal pending_ul, pending_ol, pending_quote
        if pending_ul:
            items = '\n'.join(f'<li>{item}</li>' for item in pending_ul)
            result.append(f'<ul>\n{items}\n</ul>')
            pending_ul = []
        if pending_ol:
            items = '\n'.join(f'<li>{item}</li>' for item in pending_ol)
            result.append(f'<ol>\n{items}\n</ol>')
            pending_ol = []
        if pending_quote:
            text = '<br>\n'.join(pending_quote)
            result.append(f'<blockquote>\n<p>{text}</p>\n</blockquote>')
            pending_quote = []

    def _split_table_row(line):
        """把一行表格拆成单元格（去掉首尾的 |）"""
        s = line.strip()
        if s.startswith('|'):
            s = s[1:]
        if s.endswith('|'):
            s = s[:-1]
        return [c.strip() for c in s.split('|')]

    def _is_sep_row(line):
        """判断是否为表格分隔行，如 |---|:---:|---:|"""
        s = line.strip()
        if '|' not in s:
            return False
        cells = _split_table_row(s)
        if not cells:
            return False
        return all(re.fullmatch(r':?-+:?', c) for c in cells)

    def _table_aligns(line):
        """从分隔行解析每列对齐方式，None 表示默认（不加 align 属性）"""
        aligns = []
        for c in _split_table_row(line):
            if c.startswith(':') and c.endswith(':'):
                aligns.append('center')
            elif c.startswith(':'):
                aligns.append('left')
            elif c.endswith(':'):
                aligns.append('right')
            else:
                aligns.append(None)
        return aligns

    pending_ul = []   # 缓存连续的无序列表项
    pending_ol = []   # 缓存连续的有序列表项
    pending_quote = []  # 缓存连续的引用行

    in_code_block = False
    code_lang = ""
    code_content = []
    in_mermaid = False
    mermaid_content = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # --- Mermaid 代码块 ---
        if stripped.startswith('```mermaid'):
            _flush_block()
            in_mermaid = True
            mermaid_content = []
            i += 1
            continue

        if in_mermaid:
            if stripped == '```':
                in_mermaid = False
                result.append(f'<div class="mermaid">\n{chr(10).join(mermaid_content)}\n</div>')
            else:
                mermaid_content.append(line)
            i += 1
            continue

        # --- 普通代码块 ---
        if stripped.startswith('```'):
            _flush_block()
            if not in_code_block:
                in_code_block = True
                code_lang = stripped[3:].strip()
                code_content = []
            else:
                in_code_block = False
                lang_cls = f' class="language-{code_lang}"' if code_lang else ''
                escaped = html_module.escape('\n'.join(code_content))
                result.append(f'<figure class="highlight {code_lang}"><pre><code{lang_cls}>{escaped}</code></pre></figure>')
            i += 1
            continue

        if in_code_block:
            code_content.append(line)
            i += 1
            continue

        # --- 空行：结束列表组和引用组 ---
        if not stripped:
            _flush_block()
            i += 1
            continue

        # --- 标题 ---
        if line.startswith('### '):
            _flush_block()
            text = _inline(line[4:])
            anchor = _anchor(text)
            result.append(f'<h3 id="{anchor}"><a href="#{anchor}" class="headerlink" title="{text}"></a>{text}</h3>')
        elif line.startswith('## '):
            _flush_block()
            text = _inline(line[3:])
            anchor = _anchor(text)
            result.append(f'<h2 id="{anchor}"><a href="#{anchor}" class="headerlink" title="{text}"></a>{text}</h2>')
        elif line.startswith('# '):
            _flush_block()
            text = _inline(line[2:])
            anchor = _anchor(text)
            result.append(f'<h1 id="{anchor}"><a href="#{anchor}" class="headerlink" title="{text}"></a>{text}</h1>')

        # --- 无序列表 ---
        elif re.match(r'^[\s]*[-*]\s+', line):
            _flush_block()  # 不 flush ol（不同类型列表不互斥）
            if pending_ol:
                _flush_block()
            text = _inline(re.sub(r'^[\s]*[-*]\s+', '', line))
            pending_ul.append(text)

        # --- 有序列表 ---
        elif re.match(r'^[\s]*\d+\.\s', line):
            if pending_ul:
                _flush_block()
            text = _inline(re.sub(r'^[\s]*\d+\.\s', '', line))
            pending_ol.append(text)

        # --- 引用块 ---
        elif stripped.startswith('> '):
            if pending_ul or pending_ol:
                _flush_block()
            text = _inline(stripped[2:])
            pending_quote.append(text)

        # --- 分隔线 ---
        elif stripped in ('---', '***', '___', '* * *'):
            _flush_block()
            result.append('<hr>')

        # --- Markdown 表格 ---
        elif '|' in line and i + 1 < len(lines) and _is_sep_row(lines[i + 1]):
            _flush_block()
            header_cells = [_inline(c) for c in _split_table_row(line)]
            aligns = _table_aligns(lines[i + 1])
            i += 2  # 跳过表头与分隔行

            rows = []
            while i < len(lines) and lines[i].strip() and '|' in lines[i]:
                rows.append([_inline(c) for c in _split_table_row(lines[i])])
                i += 1

            def _cell(tag, cells):
                parts = []
                for c, a in zip(cells, aligns):
                    if a:
                        parts.append(f'<{tag} align="{a}">{c}</{tag}>')
                    else:
                        parts.append(f'<{tag}>{c}</{tag}>')
                return '\n'.join(parts)

            th = _cell('th', header_cells)
            result.append(f'<table>\n<thead>\n<tr>\n{th}\n</tr>\n</thead>')

            body_html = '<tbody>'
            for row in rows:
                td = _cell('td', row)
                body_html += f'<tr>\n{td}\n</tr>'
            body_html += '</tbody></table>'
            result.append(body_html)
            continue

        # --- 普通段落 ---
        else:
            _flush_block()
            processed = _inline(stripped)
            result.append(f'<p>{processed}</p>')

        i += 1

    # 处理文件末尾未闭合的块
    _flush_block()
    if in_code_block:
        escaped = html_module.escape('\n'.join(code_content))
        result.append(f'<figure class="highlight"><pre><code>{escaped}</code></pre></figure>')

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

    # 防止重复插入：检查该 URL 是否已存在于首页
    if post_url_path in content:
        print(f"[INFO] 文章链接 {post_url_path} 已存在于首页，跳过插入")
        return

    img_src = cover_img or get_cover_image(post_title)
    preview_text = content_preview[:200].replace('"', '\\"').replace('\n', ' ')

    # 检测第一个已有文章的封面位置，新文章取反方向（左右交替）
    first_cover_match = re.search(r'post_cover (left_radius|right_radius)', content)
    if first_cover_match:
        existing_side = first_cover_match.group(1)
        new_side = 'right_radius' if existing_side == 'left_radius' else 'left_radius'
    else:
        new_side = 'left_radius'

    # 构建新文章卡片 HTML
    new_card = f'''<div class="recent-post-item"><div class="post_cover {new_side}"><a href="{post_url_path}" title="{post_title}">     <img class="post_bg" src="{img_src}" onerror="this.onerror=null;this.src='/img/404.jpg'" alt="{post_title}"></a></div><div class="recent-post-info"><a class="article-title" href="{post_url_path}" title="{post_title}">{post_title}</a><div class="article-meta-wrap"><time class="post-meta__date"><span class="post-meta__date-created" title="发表于 {date_str}"><i class="fa fa-calendar" aria-hidden="true"></i>{date_str}</span><span class="article-meta__separator">|</span><span class="post-meta__date-updated" title="更新于 {date_str}"><i class="fa fa-history" aria-hidden="true"></i>{date_str}</span></time><span class="article-meta"><span class="article-meta__separator">|</span><i class="fa fa-inbox article-meta__icon" aria-hidden="true"></i><a class="article-meta__categories" href="/categories/">{category}</a></span></div><div class="content">{preview_text}</div></div></div>'''

    # 在第一个 recent-post-item 之前插入
    first_item_pos = content.find('<div class="recent-post-item">')
    if first_item_pos == -1:
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
    print(f"[OK] 首页 index.html 已更新（封面: {new_side}）")

# ============================================================
# Slug 生成（直接用中文标题，与老文章风格一致）
# ============================================================
def generate_slug(title):
    """直接用文章标题做目录名，只替换文件系统禁止的字符"""
    # 替换文件系统不安全的字符（和旧文章 Git的常用命令/ 风格一致）
    slug = title
    for ch in ['/', '\\', ':', '*', '?', '"', '<', '>', '|']:
        slug = slug.replace(ch, '-')
    # 去掉首尾空格和点
    slug = slug.strip(' .')
    if not slug:
        slug = "untitled"
    return slug

# ============================================================
# 文件写入
# ============================================================
def write_post_files(title, category, html_content):
    """创建文章目录并写入 HTML 文件"""
    now = datetime.now(timezone(timedelta(hours=8)))
    year = now.strftime("%Y")
    month = now.strftime("%m")
    day = now.strftime("%d")

    # 直接用中文标题做目录名，与旧文章风格一致
    slug = generate_slug(title)
    post_dir = REPO_ROOT / year / month / day / slug
    post_dir.mkdir(parents=True, exist_ok=True)

    index_file = post_dir / "index.html"
    index_file.write_text(html_content, encoding="utf-8")

    print(f"[OK] 文章已生成: {post_dir}/index.html")
    url_path = f"/{year}/{month}/{day}/{slug}/"
    return url_path

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

    # Dry-run 模式：仅预览，不调用 API
    if args.dry_run:
        print(f"\n[Dry Run] 文章标题（预设）: {subtopic}")
        print(f"[Dry Run] 分类: {category}")
        print(f"[Dry Run] Prompt 前 300 字符:\n{prompt[:300]}...")
        return

    # 4. 调用 AI 生成内容
    provider = config["generation"].get("api_provider", "deepseek")
    print(f"\n[生成] 正在调用 {provider.upper()} API 生成博客内容...")
    try:
        raw_content = call_ai_api(prompt, config)
        # 后处理：切除 AI 可能输出的元信息引用块
        md_content = _clean_ai_output(raw_content)
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
    url_path = write_post_files(post_title, category, full_html)

    # 11. 提取摘要
    plain_text = re.sub(r'<[^>]+>', '', content_html)
    plain_text = re.sub(r'\s+', ' ', plain_text).strip()

    # 12. 重建静态索引（首页/归档/分类/标签/详情页/侧边栏由 rebuild_site.py 生成）
    try:
        import rebuild_site
        rebuild_site.rebuild_after_new_post()
    except Exception as e:
        print(f"[WARN] 静态重建失败: {e}")
        # 回退：仍按旧逻辑往首页静态列表插入
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
