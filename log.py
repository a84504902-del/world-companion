"""日志配置模块"""
import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging():
    """配置全局日志格式和级别

    控制台 + 文件双输出：以前只有控制台，重启即丢，出问题无据可查（2026-10-13 补）。
    文件落在 logs/app.log，5MB×3 轮换，不会撑爆磁盘。
    """
    handlers = [logging.StreamHandler()]

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, "app.log"),
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8"
        )
        handlers.append(file_handler)
    except Exception:
        pass  # 文件日志失败不阻塞启动（只打控制台）

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
