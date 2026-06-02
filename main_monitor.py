import multiprocessing
import os
import sys

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_BASE_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from quantforge.tools.log_format import format_no_exception

os.makedirs(os.path.join(_BASE_DIR, 'logs'), exist_ok=True)
from loguru import logger
logger.remove()
logger.add(sys.stdout, level='INFO',
           format='<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}')
logger.add(os.path.join(_BASE_DIR, 'logs', 'log.txt'), level='INFO', format=format_no_exception,
           encoding='utf-8', enqueue=True,
           rotation="1 MB", retention="6 months")

from quantforge.core.notifier import WeChatNotifier, EmailNotifier
from quantforge.tools.time_utils import is_stock_trading_day

# ============================================================
# 通知器实例
# ============================================================

wechat = WeChatNotifier()
email = EmailNotifier()

# ============================================================
# 策略注册  ← 注释掉某行即可关闭该策略
# ============================================================

from quantforge.monitors import roc_momentum_monitor
from quantforge.monitors import guzhai_licha_monitor
from quantforge.monitors import ah_premium_monitor

MONITOR_TASKS = [
    (roc_momentum_monitor, ([wechat, email],), "ROC_Momentum"),
    (guzhai_licha_monitor, ([wechat, email],), "Guzhai_Licha_5050"),
    (ah_premium_monitor, ([wechat, email],), "AH_Premium"),
]


def wechat_monitor():
    if not is_stock_trading_day():
        logger.info("非交易日，不启动监控")
        wechat.notify("监控状态", "非交易日，不启动监控")
        return

    logger.info("监控主程序启动")
    wechat.notify("监控状态", "监控主程序启动")

    processes = []
    for func, args, name in MONITOR_TASKS:
        try:
            p = multiprocessing.Process(target=func, args=args, name=name)
            p.start()
            processes.append(p)
            logger.info(f"子进程启动: {name}")
        except Exception as e:
            logger.error(f"启动 {name} 失败: {e}")
            wechat.notify(f"{name} 异常", f"子进程启动失败: {e}")

    for p in processes:
        p.join()
        if p.exitcode and p.exitcode != 0:
            logger.error(f"{p.name} 异常退出, exitcode={p.exitcode}, "
                         f"请检查 logs/log.txt 中的子进程日志")

    wechat.notify("监控状态", "监控主程序结束")
    logger.info("监控主程序结束")


if __name__ == "__main__":
    try:
        wechat_monitor()
    except Exception as e:
        logger.opt(exception=True).error(f"主程序运行出错: {e}")
        wechat.notify("监控异常", f"主程序运行出错：{e}")
