import os
from datetime import datetime, timedelta

from loguru import logger

from quantforge.core.data_feed import DataRequest, create_cached_feed
from quantforge.core.executor import LiveExecutor
from quantforge.core.resolver import make_ranking_resolver
from quantforge.core.style_rotator import StyleRotator
from quantforge.data_sources.autostock_feed import AutoStockFeed
from quantforge.strategies.factory import create_strategy, create_config
from quantforge.tools.time_utils import wait_until
from quantforge.monitors._shared import _BASE_DIR, CHECKPOINTS, _skip_if_passed, report_roc_signals, build_decision_report


def roc_momentum_monitor(notifiers, name="ROC_Momentum"):
    try:
        config = create_config("roc_momentum", "tech_growth")
        if config.style_rotation_enabled:
            _run_rotation(notifiers, name, config)
        else:
            _run_standard(notifiers, name, config)
    except Exception as e:
        logger.opt(exception=True).error(f"{name}: 运行异常: {e}")
        notifier = notifiers[0] if notifiers else None
        if notifier:
            notifier.notify(f"{name} 错误", f"运行异常: {e}")


def _run_rotation(notifiers, name, config):
    rotator = StyleRotator(config)

    data_feed = create_cached_feed(AutoStockFeed, os.path.join(_BASE_DIR, 'data'))

    data_feed.update_cache(
        codes=[config.sr_benchmark],
        data_type=config.data_type,
        start=config.start_date,
        end=config.end_date,
    )
    bench_response = data_feed.get_data(DataRequest(
        codes=[config.sr_benchmark],
        data_type=config.data_type,
        start=config.start_date,
        end=config.end_date,
    ))

    target_preset = rotator.evaluate(bench_response.bar_data.get(config.sr_benchmark))
    is_aggressive = target_preset == config.sr_aggressive_preset
    logger.info(f"风格轮动: {'进攻' if is_aggressive else '防守'}模式 → preset={target_preset}")

    target_config = create_config("roc_momentum", target_preset)
    target_strategy = create_strategy("roc_momentum", target_preset)

    _run_roc_loop(notifiers, name, data_feed, target_config, target_strategy)


def _run_standard(notifiers, name, config):
    data_feed = create_cached_feed(AutoStockFeed, os.path.join(_BASE_DIR, 'data'))
    strategy = create_strategy("roc_momentum", "tech_growth")
    _run_roc_loop(notifiers, name, data_feed, config, strategy)


def _run_roc_loop(notifiers, name, data_feed, config, strategy):
    notifier = notifiers[0] if notifiers else None
    code_names = config.code_names or {}
    codes = config.codes

    max_window = max(
        config.roc_n + config.roc_m,
        config.ma_period,
        config.rsi_period,
        config.macd_slow,
    )
    live_start = (datetime.now() - timedelta(days=max_window * 2)).strftime('%Y-%m-%d')
    live_end = config.end_date

    if config.inverse_vol_weight:
        weight_method = 'inverse_vol'
    elif config.BUY_AVERAGE:
        weight_method = 'equal'
    else:
        weight_method = 'signal_weight'
    resolver = make_ranking_resolver(config, weight_method)

    already_late = datetime.now().hour >= 15
    executor = LiveExecutor(
        notifier=notifier,
        position_file=os.path.join(_BASE_DIR, 'position', 'position.json'),
        initial_capital=config.initial_capital,
        code_names=code_names,
        name=name,
        dry_run=already_late,
    )

    # 回放模式：收盘后数据不变，一次性拉取+计算，只输出14:40交易操作
    if already_late:
        logger.info(f"{name} 当前时间已过15:00，进入回放模式（仅计算输出，不更新持仓）")
        data_feed.update_cache(
            codes=codes, data_type=config.data_type,
            start=live_start, end=live_end,
        )
        response = data_feed.get_data(DataRequest(
            codes=codes, data_type=config.data_type,
            start=live_start, end=live_end,
        ))
        decisions = strategy.produce_decisions(response, executor.get_positions())

        report_text = build_decision_report(decisions, name, 14, 40, code_names,
                                            held_codes=set(executor.get_positions()),
                                            positions=executor.positions,
                                            response=response)
        logger.info(report_text)

        report_roc_signals(
            notifier, decisions, name, 14, 40, code_names,
            held_codes=set(executor.get_positions()),
            positions=executor.positions,
            response=response,
            high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
            cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
        )

        targets = resolver.resolve(decisions, executor.get_positions(), executor.available_capital(), response)
        result = executor.execute(targets, response)
        logger.info(f"{name} 交易操作完成 (14:40) msg={len(result.get('messages', []))}条")

        logger.info(f"{name} 监控完成")
        return

    # 正常模式：按checkpoint循环，每次重新拉取数据
    for h, m, is_trade in CHECKPOINTS:
        if not is_trade and _skip_if_passed(h, m):
            logger.info(f"{name} {h:02d}:{m:02d} 已过时，跳过")
            continue

        wait_until(h, m)

        if datetime.now().hour >= 15:
            logger.info(f"{name} 已过15:00，退出监控循环")
            break

        data_feed.update_cache(
            codes=codes, data_type=config.data_type,
            start=live_start, end=live_end,
        )
        response = data_feed.get_data(DataRequest(
            codes=codes, data_type=config.data_type,
            start=live_start, end=live_end,
        ))

        decisions = strategy.produce_decisions(response, executor.get_positions())

        report_text = build_decision_report(decisions, name, h, m, code_names,
                                            held_codes=set(executor.get_positions()),
                                            positions=executor.positions,
                                            response=response)
        logger.info(report_text)

        if is_trade:
            report_roc_signals(
                notifier, decisions, name, h, m, code_names,
                held_codes=set(executor.get_positions()),
                positions=executor.positions,
                response=response,
                high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
                cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
            )
            targets = resolver.resolve(decisions, executor.get_positions(), executor.available_capital(), response)
            result = executor.execute(targets, response)
            logger.info(f"{name} 交易操作完成 ({h:02d}:{m:02d}) msg={len(result.get('messages', []))}条")
        else:
            report_roc_signals(
                notifier, decisions, name, h, m, code_names,
                held_codes=set(executor.get_positions()),
                positions=executor.positions,
                response=response,
                high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
                cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
            )
            logger.info(f"{name} 信号汇报完成 ({h:02d}:{m:02d})")

    logger.info(f"{name} 监控完成")
