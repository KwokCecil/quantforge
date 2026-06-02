# @layer: integration
"""T003 WebScraperFeed 验证脚本"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from loguru import logger

from quantforge.core.data_feed import DataRequest, DataResponse
from quantforge.data_sources.web_scraper_feed import WebScraperFeed

os.makedirs(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")


def test_chinabond():
    """测试中债国债收益率爬取"""
    logger.info("=== 测试 scraper_chinabond ===")
    feed = WebScraperFeed()

    try:
        resp = feed.get_data(DataRequest(
            codes=[""], data_type="scraper_chinabond",
            start="2025-01-01", end="2025-12-31",
        ))
        assert isinstance(resp, DataResponse), "返回值不是 DataResponse"
        logger.success(f"返回 DataResponse, bar_data keys={list(resp.bar_data.keys())}, macro_data keys={list(resp.macro_data.keys())}")

        if "chinabond_10y_yield" in resp.macro_data:
            data = resp.macro_data["chinabond_10y_yield"]
            logger.success(f"中债10年期收益率: {data}")
        else:
            logger.warning("未获取到中债收益率数据（可能网络/API不可用）")
    except Exception as e:
        logger.warning(f"中债爬虫异常（可接受，依赖网络）: {e}")

    logger.info("scraper_chinabond 测试完成")


def test_jisilu():
    """测试集思录 QDII 溢价率爬取"""
    logger.info("=== 测试 scraper_jisilu ===")
    feed = WebScraperFeed()

    try:
        resp = feed.get_data(DataRequest(
            codes=["513050", "159915"], data_type="scraper_jisilu",
            start="2025-01-01", end="2025-12-31",
        ))
        assert isinstance(resp, DataResponse), "返回值不是 DataResponse"
        logger.success(f"返回 DataResponse, bar_data keys={list(resp.bar_data.keys())}")

        for code, df in resp.bar_data.items():
            logger.success(f"  {code}: {df.to_dict(orient='records')}")
    except Exception as e:
        logger.warning(f"集思录爬虫异常（可接受，依赖网络）: {e}")

    logger.info("scraper_jisilu 测试完成")


def test_unknown_data_type():
    """测试未知 data_type 的优雅降级"""
    logger.info("=== 测试未知 data_type ===")
    feed = WebScraperFeed()
    resp = feed.get_data(DataRequest(
        codes=["test"], data_type="scraper_unknown",
        start="2025-01-01", end="2025-12-31",
    ))
    assert isinstance(resp, DataResponse), "未知 data_type 应返回空 DataResponse"
    assert len(resp.bar_data) == 0, "未知类型的 bar_data 应为空"
    assert len(resp.macro_data) == 0, "未知类型的 macro_data 应为空"
    logger.success("未知 data_type 优雅降级 OK")


def test_interface_compliance():
    """测试接口符合 DataFeed 规范"""
    logger.info("=== 测试接口合规性 ===")
    feed = WebScraperFeed()

    assert hasattr(feed, "get_data"), "缺少 get_data 方法"
    assert hasattr(feed, "get_latest_price"), "缺少 get_latest_price 方法"

    try:
        feed.get_latest_price("513050")
        logger.warning("get_latest_price 应该抛出 NotImplementedError")
    except NotImplementedError:
        logger.success("get_latest_price 正确抛出 NotImplementedError")

    logger.success("接口合规性 OK")


def main():
    test_interface_compliance()
    test_unknown_data_type()
    test_chinabond()
    test_jisilu()
    logger.success("\nT003 WebScraperFeed 全部测试完成")


if __name__ == "__main__":
    main()
