# 小曾博客 - 自动生成系统

## 目录结构

```
auto_blog/
├── config.json          # 话题配置（权重、子话题、标签等）
├── generate_post.py     # 核心脚本：选题→AI生成→HTML→Git推送
├── run_daily.py         # 每日运行包装器（含日志记录）
├── publish_first.py     # 首次发布工具（从markdown文件生成）
├── setup_scheduler.bat  # Windows任务计划器配置脚本（需管理员权限）
├── .apikey              # 存放 Anthropic API Key（仅一行）
├── history.json         # 已生成文章记录（自动维护）
├── logs/                # 运行日志目录（自动创建）
└── requirements.txt     # 依赖说明（纯标准库，无需额外安装）
```

## 使用说明

### 1. 配置 API Key

获取 Anthropic API Key：https://console.anthropic.com/settings/keys

**方式一**：写入 `.apikey` 文件（仅一行）
```
echo "sk-ant-xxx" > auto_blog/.apikey
```

**方式二**：设置环境变量
```
set ANTHROPIC_API_KEY=sk-ant-xxx
```

### 2. 手动运行

```bash
# 生成并发布一篇博客
cd D:\xiaozeng26.github.io
D:\Python\Python3.14\python auto_blog\run_daily.py
```

### 3. 定时任务

任务已通过 Windows 任务计划器配置：
- **任务名**：AutoBlogDaily
- **运行时间**：每天 20:00
- **运行脚本**：D:\xiaozeng26.github.io\run_blog.bat

**管理命令**：
```cmd
# 查看任务
schtasks /query /tn AutoBlogDaily

# 手动触发测试
schtasks /run /tn AutoBlogDaily

# 删除任务
schtasks /delete /tn AutoBlogDaily /f
```

### 4. 自定义话题配置

编辑 `config.json`：

```json
{
  "topic_weights": {
    "java": 25,        // 权重越高，被选中的概率越大
    "python": 20,
    "go": 20,
    "docker_kubernetes": 15,
    "ai": 15,
    "architecture": 15
  },
  "topics": {
    "java": {
      "subtopics": [
        "在这里添加新话题..."
      ]
    }
  }
}
```

### 5. 日志查看

运行日志保存在 `auto_blog/logs/` 目录下，每天生成一个日志文件。

## 访问博客

- 博客地址：https://xiaozeng26.github.io
- GitHub 仓库：https://github.com/xiaozeng26/xiaozeng26.github.io
