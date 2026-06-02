import time
from datetime import datetime, timedelta
from loguru import logger


def is_stock_trading_day(date: datetime | None = None) -> bool:
    """判断是否为A股交易日。先排除周末，再用chinese_calendar排除法定节假日。"""
    if date is None:
        date = datetime.today()
    if date.weekday() >= 5:
        return False
    try:
        import chinese_calendar
        if chinese_calendar.is_holiday(date):
            return False
    except ImportError:
        logger.warning("chinese_calendar 未安装，仅按周末判断交易日")
    return True


def get_trading_dates(start: str, end: str) -> list[str]:
    """获取指定区间内的所有交易日列表。"""
    start_dt = datetime.strptime(start, '%Y-%m-%d')
    end_dt = datetime.strptime(end, '%Y-%m-%d')

    dates = []
    current = start_dt
    while current <= end_dt:
        if is_stock_trading_day(current):
            dates.append(current.strftime('%Y-%m-%d'))
        current += timedelta(days=1)
    return dates


def wait_until(hour: int, minute: int, second: int = 0):
    """等待到指定时间。如果目标时间已过，立即返回（不等待）。"""
    target = datetime.now().replace(hour=hour, minute=minute, second=second, microsecond=0)
    now = datetime.now()
    if now >= target:
        return
    sleep_time = (target - now).total_seconds()
    logger.info(f"等待至 {hour:02d}:{minute:02d}:{second:02d}，还需 {sleep_time:.0f} 秒")
    time.sleep(sleep_time)
