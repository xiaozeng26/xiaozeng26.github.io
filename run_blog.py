#!/usr/bin/env python3
"""
每日博客自动运行入口（跨平台：Windows / macOS / Linux）
替代 run_blog.bat，在任何操作系统上都能直接运行。

用法:
    python run_blog.py              # 直接运行
    python run_blog.py --schedule   # 配置系统定时任务（macOS/Linux用cron, Windows用schtasks）
"""

import sys
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
AUTO_BLOG_DIR = SCRIPT_DIR / "auto_blog"
LOG_DIR = AUTO_BLOG_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


def log(msg: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)


def run_generate() -> int:
    """调用生成脚本"""
    import subprocess

    generate_script = AUTO_BLOG_DIR / "generate_post.py"
    if not generate_script.exists():
        log(f"[ERROR] 找不到 {generate_script}")
        return 1

    log("启动博客自动生成...")
    result = subprocess.run(
        [sys.executable, str(generate_script)],
        capture_output=False,
        timeout=600,
    )
    log(f"完成，返回码: {result.returncode}")
    return result.returncode


def setup_schedule():
    """配置系统定时任务（每天 20:00）"""
    import platform

    system = platform.system()
    py_path = sys.executable
    script_path = str(Path(__file__).resolve())

    if system == "Windows":
        _setup_windows(py_path, script_path)
    elif system == "Darwin":
        _setup_mac(py_path, script_path)
    else:
        _setup_linux(py_path, script_path)


def _setup_windows(py_path: str, script_path: str):
    """Windows 任务计划器"""
    import subprocess

    task_name = "AutoBlogDaily"
    cmd = (
        f'schtasks /create /tn {task_name} '
        f'/tr "\\"{py_path}\\" \\"{script_path}\\"" '
        f'/sc DAILY /st 20:00 /f'
    )
    print(f"创建 Windows 计划任务: {task_name}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode == 0:
        print(f"✅ 任务已创建，每天 20:00 自动运行")
    else:
        print("❌ 创建失败，请以管理员身份运行此脚本")


def _setup_mac(py_path: str, script_path: str):
    """macOS 使用 launchd（比 cron 更可靠）"""
    import subprocess

    plist_name = "com.xiaozeng.dailyblog.plist"
    plist_path = Path.home() / "Library" / "LaunchAgents" / plist_name

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.xiaozeng.dailyblog</string>
    <key>ProgramArguments</key>
    <array>
        <string>{py_path}</string>
        <string>{script_path}</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>20</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{LOG_DIR}/launchd_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>{LOG_DIR}/launchd_stderr.log</string>
    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>"""

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(plist_content)
    print(f"已创建 launchd 配置: {plist_path}")

    subprocess.run(["launchctl", "unload", str(plist_path)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(plist_path)], capture_output=True)

    if result.returncode == 0:
        print("✅ macOS 定时任务已配置，每天 20:00 自动运行")
        print(f"   查看状态: launchctl list | grep xiaozeng")
        print(f"   删除任务: launchctl unload {plist_path}")
    else:
        print("❌ 配置失败，手动方案:")
        print(f"   在终端执行: crontab -e")
        print(f"   添加: 0 20 * * * {py_path} {script_path}")


def _setup_linux(py_path: str, script_path: str):
    """Linux 使用 cron"""
    cron_line = f"0 20 * * * {py_path} {script_path} >> {LOG_DIR}/cron.log 2>&1"
    print("Linux 定时任务配置：")
    print(f"  在终端执行: crontab -e")
    print(f"  添加以下行:")
    print(f"  {cron_line}")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--schedule":
        setup_schedule()
    else:
        log("=" * 50)
        log("每日博客自动生成")
        exit_code = run_generate()
        log("=" * 50)
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
