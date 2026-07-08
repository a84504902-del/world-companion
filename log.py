"""日志配置模块"""
import logging


def setup_logging():
    """配置全局日志格式和级别"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S"
    )
