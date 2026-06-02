# @layer: unit
"""T032 辅助信号卖出端 — _evaluate 卖出规则 单元测试"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

from quantforge.strategies._configs.roc_config import ROCConfig
from quantforge.strategies.roc_momentum import ROCStrategy


def _make_config(**kw):
    cfg = ROCConfig()
    # 开启但让买入阈值极高，避免干扰
    cfg.buy_roc_edge = 999.0
    cfg.codes = ["510300"]
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def _make_strategy(config):
    return ROCStrategy(config)


def test_volume_sell_triggers():
    """持有中 vol_ratio >= 1.5 时触发放量卖出。"""
    cfg = _make_config(volume_sell_enabled=True, volume_sell_spike_ratio=1.5)
    s = _make_strategy(cfg)
    ind = {'roc': 50.0, 'maroc': 30.0, 'hold_vol_ratio': 2.0}
    direction, _, reason = s._evaluate("510300", 50.0, ind, {"510300": {}})
    assert direction == 'exit', f"应触发放量卖出, got {direction}"
    assert "放量" in reason


def test_volume_sell_no_trigger_below():
    """vol_ratio < 1.5 时不触发。"""
    cfg = _make_config(volume_sell_enabled=True, volume_sell_spike_ratio=1.5)
    s = _make_strategy(cfg)
    ind = {'roc': 50.0, 'maroc': 30.0, 'hold_vol_ratio': 1.2}
    direction, _, _ = s._evaluate("510300", 50.0, ind, {"510300": {}})
    assert direction != 'exit', f"不应触发, got {direction}"


def test_atr_expansion_sell_triggers():
    """持有中 ATR扩张 时触发退出。"""
    cfg = _make_config(atr_expansion_sell_enabled=True)
    s = _make_strategy(cfg)
    ind = {'roc': 50.0, 'maroc': 30.0, 'hold_atr_expansion': True}
    direction, _, reason = s._evaluate("510300", 50.0, ind, {"510300": {}})
    assert direction == 'exit'
    assert "ATR" in reason


def test_macd_divergence_sell_triggers():
    """持有中 MACD顶背离 时触发退出。"""
    cfg = _make_config(macd_divergence_sell_enabled=True)
    s = _make_strategy(cfg)
    ind = {'roc': 50.0, 'maroc': 30.0, 'hold_macd_divergence': True}
    direction, _, reason = s._evaluate("510300", 50.0, ind, {"510300": {}})
    assert direction == 'exit'
    assert "背离" in reason


def test_rsi_sell_triggers():
    """持有中 RSI>80 时触发止盈。"""
    cfg = _make_config(rsi_sell_enabled=True)
    s = _make_strategy(cfg)
    ind = {'roc': 50.0, 'maroc': 30.0, 'hold_rsi': 85.0}
    direction, _, reason = s._evaluate("510300", 50.0, ind, {"510300": {}})
    assert direction == 'exit'
    assert "RSI" in reason


def test_rsi_sell_no_trigger_below_80():
    """RSI<=80 时不触发止盈。"""
    cfg = _make_config(rsi_sell_enabled=True)
    s = _make_strategy(cfg)
    ind = {'roc': 50.0, 'maroc': 30.0, 'hold_rsi': 72.0}
    direction, _, _ = s._evaluate("510300", 50.0, ind, {"510300": {}})
    assert direction != 'exit', f"RSI=72不应触发"


def test_sell_only_affects_positions():
    """辅助卖出只对持仓标的生效，不对非持仓标的。"""
    cfg = _make_config(volume_sell_enabled=True)
    s = _make_strategy(cfg)
    ind = {'roc': 50.0, 'maroc': 30.0, 'hold_vol_ratio': 2.0}
    # 非持仓：应该走到买入判断但不触发（因为buy_roc_edge=999）
    direction, _, _ = s._evaluate("510300", 50.0, ind, {})
    assert direction != 'exit', "非持仓不应被卖出"


def test_all_sell_off_does_not_trigger():
    """所有开关关闭时，持有中 vol_ratio 高也不触发。"""
    cfg = _make_config(volume_sell_enabled=False, atr_expansion_sell_enabled=False,
                       macd_divergence_sell_enabled=False, rsi_sell_enabled=False)
    s = _make_strategy(cfg)
    ind = {'roc': 50.0, 'maroc': 50.0, 'hold_vol_ratio': 3.0, 'hold_atr_expansion': True,
           'hold_macd_divergence': True, 'hold_rsi': 90.0}
    direction, _, _ = s._evaluate("510300", 50.0, ind, {"510300": {}})
    # maroc=50 > 0, roc=50 > 3.0, 所以持仓观望
    assert direction == 'hold', f"全关时应hold, got {direction}"


def test_existing_sell_signals_still_work():
    """roc < sell_roc_edge 仍然触发，不受新开关影响。"""
    cfg = _make_config(sell_roc_edge=5.0, volume_sell_enabled=True)
    s = _make_strategy(cfg)
    ind = {'roc': 2.0, 'maroc': 30.0, 'hold_vol_ratio': 0.5}
    direction, _, reason = s._evaluate("510300", 2.0, ind, {"510300": {}})
    assert direction == 'exit'
    assert "ROC" in reason


if __name__ == "__main__":
    test_volume_sell_triggers();           print("PASS 放量卖出触发")
    test_volume_sell_no_trigger_below();   print("PASS 放量卖出不触发")
    test_atr_expansion_sell_triggers();    print("PASS ATR扩张卖出触发")
    test_macd_divergence_sell_triggers();  print("PASS MACD背离卖出触发")
    test_rsi_sell_triggers();              print("PASS RSI止盈触发")
    test_rsi_sell_no_trigger_below_80();   print("PASS RSI低不触发")
    test_sell_only_affects_positions();    print("PASS 非持仓不卖出")
    test_all_sell_off_does_not_trigger();  print("PASS 全关无影响")
    test_existing_sell_signals_still_work(); print("PASS 原有卖出仍生效")
    print("\nALL 9 TESTS PASSED")
