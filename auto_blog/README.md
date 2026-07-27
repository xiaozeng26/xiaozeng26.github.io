# 小曾博客 - 自动生成 & 发布系统

## 架构概览

```
GitHub Actions (云端, 每天20:00触发)
    │
    ├─→ 调用 Claude API 生成博客内容
    ├─→ 生成 HTML 页面（匹配现有 Hexo 模板）
    ├─→ 更新首页文章列表
    ├─→ Git commit & push → GitHub Pages 自动部署
    │
    └─→ 访问: https://xiaozeng26.github.io
```

**核心优势**：workflow 文件随仓库一起克隆，换任何电脑都不需要重新配置定时任务。只要仓库在，每天自动运行。

---

## 快速开始

### 第一步：获取 API Key

1. 打开 https://console.anthropic.com/ → 注册/登录
2. 点击 **Settings → API Keys**
3. 点击 **Create Key**，复制 `sk-ant-api03-xxx...`

> 费用预估：每篇博客约 $0.03-0.10，一个月约 $1-3

### 第二步：配置到 GitHub（必须）

1. 打开仓库 Secrets 页面：
   ```
   https://github.com/xiaozeng26/xiaozeng26.github.io/settings/secrets/actions
   ```
2. 点击 **New repository secret**
3. Name 填：`ANTHROPIC_API_KEY`
4. Value 填：你的 API Key（`sk-ant-api03-xxx...`）
5. 点击 **Add secret**

> 配置后，GitHub Actions 每天 20:00 就能自动调用 AI 生成博客了。

### 第三步：手动触发测试

1. 打开 Actions 页面：
   ```
   https://github.com/xiaozeng26/xiaozeng26.github.io/actions
   ```
2. 点击左侧 **Daily Blog Generation**
3. 点击右侧 **Run workflow → Run workflow**
4. 等待 ~2 分钟，查看结果

---

## 本地运行（可选）

如果想在本地手动生成一篇：

### 方式一：设置环境变量
```powershell
set ANTHROPIC_API_KEY=sk-ant-api03-xxx
cd D:\xiaozeng26.github.io\auto_blog
python generate_post.py
```

### 方式二：写入密钥文件
```powershell
echo sk-ant-api03-xxx > D:\xiaozeng26.github.io\auto_blog\.apikey
cd D:\xiaozeng26.github.io\auto_blog
python generate_post.py
```

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
├── run_blog.bat               # Windows 定时任务入口
├── index.html                 # 博客首页（自动更新）
├── 2026/07/27/...             # 生成的文章目录
└── img/                       # 封面图片资源
```
