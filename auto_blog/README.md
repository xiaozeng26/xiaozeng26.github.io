# 小曾博客 - 自动生成 & 发布系统

## 架构概览

```
GitHub Actions (云端, 每天20:00触发)
    │
    ├─→ 调用 DeepSeek API 生成博客内容
    ├─→ 生成 HTML 页面（匹配现有 Hexo 模板）
    ├─→ 更新首页文章列表
    ├─→ Git commit & push → GitHub Pages 自动部署
    │
    └─→ 访问: https://xiaozeng26.github.io
```

**默认 AI Provider: DeepSeek**（也支持切换到 Claude，修改 config.json 即可）

---

## 快速开始

### 第一步：获取 DeepSeek API Key

1. 打开 https://platform.deepseek.com/api_keys → 注册/登录
2. 点击 **创建 API Key**，复制 `sk-xxx...`

> DeepSeek 价格极低：约 ￥1/百万 tokens，每篇博客约 ￥0.01-0.02

### 第二步：配置到 GitHub

1. 打开仓库 Secrets 页面：
   ```
   https://github.com/xiaozeng26/xiaozeng26.github.io/settings/secrets/actions
   ```
2. 点击 **New repository secret**
3. **Name** 填：`DEEPSEEK_API_KEY`
4. **Value** 填：你的 DeepSeek API Key（`sk-xxx...`）
5. 点击 **Add secret**

### 第三步：手动触发测试

1. 打开 Actions 页面：
   ```
   https://github.com/xiaozeng26/xiaozeng26.github.io/actions
   ```
2. 点击左侧 **Daily Blog Generation**
3. 点击右侧 **Run workflow → Run workflow**
4. 等待 ~2 分钟，查看文章是否生成成功

---

## 本地运行（跨平台：Windows / macOS / Linux）

### 一次性：配置 API Key
```bash
echo DEEPSEEK_API_KEY=sk-xxx > auto_blog/.apikey
```

### 手动运行
```bash
python run_blog.py                    # 生成一篇并推送
python auto_blog/generate_post.py     # 直接调用核心脚本
```

### 配置本地定时任务（每天 20:00）
```bash
python run_blog.py --schedule        # 自动检测系统并配置定时任务
```

| 系统 | 定时方式 |
|------|---------|
| macOS | launchd（`~/Library/LaunchAgents/`） |
| Linux | crontab（`crontab -e` 手动添加） |
| Windows | 任务计划器（`schtasks`） |

> **换电脑后只需要两步**：① `git clone` ② `echo KEY > .apikey && python run_blog.py --schedule`

---

## 切换 AI Provider

编辑 `auto_blog/config.json` 中 `generation` 部分：

```json
// 使用 DeepSeek（默认）
"generation": {
    "api_provider": "deepseek",
    "model": "deepseek-chat",
    ...
}

// 切换到 Claude（需要能注册 Anthropic 账号）
"generation": {
    "api_provider": "claude",
    "model": "claude-sonnet-4-20250514",
    ...
}
```

切换后对应的 GitHub Secret 也需要改为 `ANTHROPIC_API_KEY`。

---

## 自定义话题

编辑 `auto_blog/config.json`：

```json
{
  "topic_weights": {
    "java": 25,             // 权重越高越容易被选中
    "python": 20,
    "go": 20,
    "docker_kubernetes": 15,
    "ai": 15,               // 包含 LangChain、LangGraph 等
    "architecture": 15
  },
  "topics": {
    "java": {
      "category": "java",                           // 英文分类名
      "tags": ["java", "jvm", "spring", "concurrency"],
      "subtopics": [
        "在这里添加新话题...",
        "Java 21虚拟线程深度解析"
      ]
    }
  }
}
```

### 分类标签一览

| 大类 | 分类名 | 标签 |
|------|--------|------|
| Java | `java` | java, jvm, spring, concurrency, source-code |
| Python | `python` | python, fastapi, async, django, source-code |
| Go | `golang` | go, golang, concurrency, microservices, source-code |
| Docker/K8s | `cloud-native` | docker, kubernetes, cloud-native, devops, container |
| AI/ML | `ai-ml` | ai, machine-learning, deep-learning, llm, langchain, langgraph, rag |
| 架构 | `architecture` | architecture, distributed-systems, high-availability, design-patterns, microservices |

---

## 去重机制

系统有四重防重复机制：

| 机制 | 说明 |
|------|------|
| 子话题轮换 | 同一分类的子话题用完一轮才会重置 |
| 分类冷却 | 同一分类 3 天内不重复出现 |
| 标题去重 | 检查是否已生成过相同标题 |
| 内容哈希 | SHA256 哈希对比，防止 API 生成相同内容 |

---

## Windows 本地定时任务（备选方案）

如果 GitHub Actions 不可用，也可用 Windows 任务计划器：

```cmd
# 查看任务
schtasks /query /tn AutoBlogDaily

# 手动测试
schtasks /run /tn AutoBlogDaily

# 删除
schtasks /delete /tn AutoBlogDaily /f
```

---

## 目录结构

```
D:\xiaozeng26.github.io\
├── .github/workflows/
│   └── daily-blog.yml        # GitHub Actions 工作流（核心）
├── auto_blog/
│   ├── config.json            # 话题 & 分类配置
│   ├── generate_post.py       # 生成引擎
│   ├── run_daily.py           # 本地运行包装器
│   ├── history.json           # 已生成记录（自动维护）
│   ├── .apikey                # 本地 API Key（仅本地使用）
│   └── logs/                  # 运行日志
├── run_blog.py                # 跨平台运行入口（替代旧的 .bat）
├── index.html                 # 博客首页（自动更新）
├── 2026/07/27/...             # 生成的文章目录
└── img/                       # 封面图片资源
```
