"""清理旧备份，只保留最近 N 个（默认 5）

用法：
    venv\\Scripts\\python.exe clean_backups.py        # 保留 5 个
    venv\\Scripts\\python.exe clean_backups.py 3      # 保留 3 个

清理两类（各自独立计数，互不影响）：
  - memory_*.db     程序每次启动自动生成的数据库备份
  - before_* 目录   每次改动前手动生成的完整备份（数据库 + 源文件）

注意：排序必须按**文件名里的时间戳**，不能按字母序——
目录名前缀不同（before_ui / before_modal / before_carousel …），
按字母序会把新的当成旧的删掉。这个坑踩过一次。
"""
import os
import re
import shutil
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
KEEP = int(sys.argv[1]) if len(sys.argv) > 1 else 5


def _ts(name):
    """从文件名/目录名里提取 YYYYMMDD_HHMMSS 用于排序"""
    m = re.search(r"(\d{8}_\d{6})", name)
    return m.group(1) if m else "0"


def main():
    if not os.path.isdir(BACKUP_DIR):
        print("没有 backups 目录")
        return

    files = sorted(
        [f for f in os.listdir(BACKUP_DIR)
         if f.startswith("memory_") and f.endswith(".db")],
        key=_ts)
    dirs = sorted(
        [d for d in os.listdir(BACKUP_DIR)
         if d.startswith("before_") and os.path.isdir(os.path.join(BACKUP_DIR, d))],
        key=_ts)

    removed = 0
    for f in files[:-KEEP]:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
            print("删除备份文件:", f)
            removed += 1
        except Exception as e:
            print("删除失败:", f, e)

    for d in dirs[:-KEEP]:
        try:
            shutil.rmtree(os.path.join(BACKUP_DIR, d))
            print("删除备份目录:", d)
            removed += 1
        except Exception as e:
            print("删除失败:", d, e)

    print()
    print(f"已清理 {removed} 个，各保留最近 {KEEP} 个")
    print("当前备份：")
    for x in sorted(os.listdir(BACKUP_DIR), key=_ts):
        print("  ", "[目录]" if os.path.isdir(os.path.join(BACKUP_DIR, x)) else "[文件]", x)


if __name__ == "__main__":
    main()
