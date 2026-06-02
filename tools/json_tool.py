import json
import os
from typing import Any

from loguru import logger


def read_json(filepath: str) -> Any:
    if not os.path.exists(filepath):
        return None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"读取JSON失败 {filepath}: {e}")
        return None


def write_json(filepath: str, data: dict | list):
    os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_fund_data(cache_dir: str, code: str) -> Any:
    """读取基金缓存数据。文件名格式: fund_data_{code}.json"""
    filepath = os.path.join(cache_dir, f"fund_data_{code}.json")
    return read_json(filepath)


def write_fund_data(cache_dir: str, code: str, data: list[dict]):
    os.makedirs(cache_dir, exist_ok=True)
    filepath = os.path.join(cache_dir, f"fund_data_{code}.json")
    write_json(filepath, data)


def read_batch_params(cache_dir: str) -> Any:
    """读取批量参数追踪文件。记录各基金已缓存数据的实际日期范围。"""
    filepath = os.path.join(cache_dir, "batch_params.json")
    return read_json(filepath)


def write_batch_params(cache_dir: str, data: dict):
    os.makedirs(cache_dir, exist_ok=True)
    filepath = os.path.join(cache_dir, "batch_params.json")
    write_json(filepath, data)
