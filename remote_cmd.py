import json
import os
import subprocess
import sys
from datetime import date

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_BASE_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from quantforge.core.notifier import WeChatNotifier

notifier = WeChatNotifier()
report_str = ''


def add_content(content: str):
    global report_str
    report_str += content + '\n'


def _git_force_sync_main():
    """force sync main 分支，用于远端历史被 force push 覆写后同步"""
    add_content('>>> 开始强制同步 main 分支...')

    steps = [
        ('fetch', ['git', 'fetch', 'origin']),
        ('checkout main', ['git', 'checkout', 'main']),
        ('reset to origin/main', ['git', 'reset', '--hard', 'origin/main']),
    ]

    for name, cmd in steps:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=_BASE_DIR)
        if result.returncode != 0:
            err = result.stderr.strip()[-300:]
            add_content(f'git {name} 失败 (code={result.returncode}):\n{err}')
            return
        output = result.stdout.strip() or result.stderr.strip()
        add_content(f'git {name}: {output[:200]}')

    log_result = subprocess.run(
        ['git', 'log', '--oneline', '-3'],
        capture_output=True, text=True, cwd=_BASE_DIR,
    )
    add_content(f'当前 main 最新 3 个提交:\n{log_result.stdout.strip()}')
    add_content('main 分支强制同步完成')


def _sync_position():
    """将嵌入的持仓数据写入 position/position.json

    示例格式：
    "588000": {"shares": 10000, "avg_cost": 1.000, "high_watermark": 1.100, "prev_close": 1.050}
    """
    data = {
        "free_capital": 0,
        "last_update": "2099-01-01",
    }

    pos_dir = os.path.join(_BASE_DIR, 'position')
    os.makedirs(pos_dir, exist_ok=True)
    pos_path = os.path.join(pos_dir, 'position.json')
    with open(pos_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    add_content(f'持仓数据已写入 {pos_path}')


def remote_cmd():
    add_content('开始执行 remote_cmd.py')

    TARGET = date(2099, 1, 1)
    today = date.today()
    if today != TARGET:
        add_content(f'今天 {today} 不是目标日期 {TARGET}，跳过')
        return

    add_content(f'匹配目标日期 {TARGET}，开始强制同步 main')
    _git_force_sync_main()
    _sync_position()
    add_content('remote_cmd.py 执行完毕')


if __name__ == '__main__':
    try:
        remote_cmd()
    except Exception as e:
        add_content(f'执行 remote_cmd 时出错：{e}')
    finally:
        notifier.notify("远程维护", report_str)
