# @layer: unit
"""Decision / StrategyConfig 数据类型回归测试：默认值、序列化往返。"""
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime
from quantforge.core.decision import Decision, DecisionType
from quantforge.core.config import StrategyConfig

NOW = datetime.now()


def test_decision_defaults():
    d = Decision(DecisionType.ROTATION, NOW, "test")
    assert d.decision_type == DecisionType.ROTATION
    assert d.target_code == ""
    assert d.direction == ""
    assert d.weight == 0.0
    assert d.priority == 0
    assert d.confidence == 1.0
    assert d.strategy_name == ""
    assert d.extra == {}
    assert d.indicator_values == {}


def test_decision_extra_and_indicators():
    d = Decision(DecisionType.TIMING, NOW, "macro", "", "", 0, 0,
                 extra={"erp": 5.2},
                 indicator_values={"percentile": 80, "erp": 5.2})
    assert d.extra["erp"] == 5.2
    assert d.indicator_values["percentile"] == 80


def test_config_roundtrip():
    config = StrategyConfig(strategy_name="roc_momentum")
    d = config.to_dict()
    assert d["strategy_name"] == "roc_momentum"

    restored = StrategyConfig.from_dict(d)
    assert restored.strategy_name == "roc_momentum"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(d, f)
        tmp_path = f.name

    try:
        loaded = StrategyConfig.from_json(tmp_path)
        assert loaded.strategy_name == "roc_momentum"
    finally:
        os.unlink(tmp_path)


if __name__ == "__main__":
    test_decision_defaults();        print("PASS test_decision_defaults")
    test_decision_extra_and_indicators(); print("PASS test_decision_extra_and_indicators")
    test_config_roundtrip();         print("PASS test_config_roundtrip")
    print("\nALL 3 TESTS PASSED")
