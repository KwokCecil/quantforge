import time
import functools
from loguru import logger


def retry(max_retries: int = 3, delay: float = 1.0):
    """重试装饰器。API调用失败时自动重试，最多max_retries次，间隔delay秒。"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for i in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    logger.warning(f"{func.__name__} 第{i+1}次重试失败: {e}")
                    if i < max_retries - 1:
                        time.sleep(delay)
            if last_exception is not None:
                raise last_exception
        return wrapper
    return decorator
