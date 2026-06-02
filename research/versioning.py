"""回测结果版本化管理：Git信息注入、运行索引维护"""
import json
import os
import subprocess
from datetime import datetime

from loguru import logger


def get_git_info(repo_path: str = None) -> dict:
    """获取当前 Git 仓库信息。非 Git 环境优雅降级为 unknown。

    Returns: {'sha': str, 'branch': str, 'dirty': bool}
    """
    try:
        sha = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            cwd=repo_path, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        branch = subprocess.check_output(
            ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
            cwd=repo_path, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        dirty = subprocess.check_output(
            ['git', 'status', '--porcelain'],
            cwd=repo_path, text=True, stderr=subprocess.DEVNULL,
        ).strip() != ''
        return {'sha': sha, 'branch': branch, 'dirty': dirty}
    except Exception:
        return {'sha': 'unknown', 'branch': 'unknown', 'dirty': False}


def save_git_info(run_dir: str, repo_path: str = None):
    """将 Git 信息保存到回测运行目录的 git_info.json。"""
    info = get_git_info(repo_path)
    info['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    filepath = os.path.join(run_dir, 'git_info.json')
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

    if info['dirty']:
        logger.warning("当前工作目录有未提交修改，回测结果可能无法精确复现")

    return info


def update_run_index(run_dir: str, run_info: dict, results_dir: str = None):
    """更新回测结果索引 index.json。追加模式，不重写整个文件。

    Args:
        run_dir: 本次运行的目录路径
        run_info: {'run_id': str, 'sha': str, 'branch': str, 'dirty': bool,
                    'strategy': str, 'preset': str, ...metrics...}
        results_dir: results/ 目录路径，默认从 run_dir 推导
    """
    if results_dir is None:
        results_dir = os.path.dirname(run_dir)

    os.makedirs(results_dir, exist_ok=True)
    index_path = os.path.join(results_dir, 'index.json')

    entries = []
    if os.path.exists(index_path):
        try:
            with open(index_path, 'r', encoding='utf-8') as f:
                entries = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            entries = []

    entries.append(run_info)

    with open(index_path, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    logger.info(f"回测索引已更新: {index_path} (共 {len(entries)} 条记录)")
