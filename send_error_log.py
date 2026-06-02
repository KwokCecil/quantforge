import os
import sys
import time

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_BASE_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from quantforge.core.notifier import WeChatNotifier

notifier = WeChatNotifier()


def send_error_log():
    for log_file, label in [
        ('logs/log_error.txt', '日志错误'),
        ('logs/runtime_error.txt', '运行错误'),
    ]:
        while True:
            try:
                with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                if len(content) == 0:
                    content = '（空）'
                notifier.notify(label, content)
                open(log_file, 'w').close()
                break
            except FileNotFoundError:
                break
            except Exception as e:
                time.sleep(300)


if __name__ == "__main__":
    send_error_log()
