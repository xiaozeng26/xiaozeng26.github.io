#!/usr/bin/env python3
"""
每日博客自动生成运行器
用法: python run_daily.py

此脚本会:
1. 记录运行日志
2. 调用 generate_post.py 生成一篇博客
3. 将结果写入日志文件
"""

import subprocess
import sys
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

log_file = LOG_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

def log(msg):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def main():
    log("=" * 50)
    log("每日博客自动生成启动")
    log(f"日志文件: {log_file}")

    try:
        # 切换到脚本所在目录
        import os
        os.chdir(str(SCRIPT_DIR))

        # 调用生成脚本
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "generate_post.py")],
            capture_output=True,
            text=True,
            timeout=600,  # 10分钟超时
            encoding="utf-8"
        )

        log("STDOUT:")
        for line in result.stdout.split('\n'):
            log(f"  {line}")

        if result.stderr:
            log("STDERR:")
            for line in result.stderr.split('\n'):
                log(f"  {line}")

        if result.returncode == 0:
            log("✅ 博客生成成功")
        else:
            log(f"❌ 博客生成失败，返回码: {result.returncode}")

    except subprocess.TimeoutExpired:
        log("❌ 博客生成超时（超过10分钟）")
    except Exception as e:
        log(f"❌ 运行异常: {e}")
        import traceback
        log(traceback.format_exc())

    log("每日博客自动生成结束")
    log("=" * 50)

if __name__ == "__main__":
    main()
