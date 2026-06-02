# @layer: unit
import numpy as np
import pandas as pd
from quantforge.research.factor_lab import FactorLab

np.random.seed(42)
n = 500
fv_a = pd.Series(np.random.randn(n) * 0.1)
fr_a = pd.Series(fv_a * 0.3 + np.random.randn(n) * 0.05)

result = FactorLab.compute_ic({"A": fv_a}, {"A": fr_a})
print("=== compute_ic ===")
for k, v in result.items():
    if k == "ic_series":
        print(f"{k}: length={len(v)}, mean={v.mean():.4f}")
    else:
        print(f"{k}: {v:.4f}")

assert "ic_mean" in result, "缺少 ic_mean"
assert "ic_std" in result, "缺少 ic_std"
assert "icir" in result, "缺少 icir"
assert "ic_positive_ratio" in result, "缺少 ic_positive_ratio"
assert len(result["ic_series"]) == 1, f"应返回1个IC值（1个标的）: {len(result['ic_series'])}"
assert result["ic_mean"] > 0.15, f"IC均值偏低: {result['ic_mean']:.4f} (因子→收益已知正相关)"

lb = FactorLab.layered_backtest({"A": fv_a}, {"A": fr_a}, n_groups=5)
print()
print("=== layered_backtest ===")
print("group_returns:", {k: f"{v:.4f}" for k, v in lb["group_returns"].items()})
print("is_monotonic:", lb["is_monotonic"])
print("spread:", f"{lb['spread']:.4f}")

assert "group_returns" in lb, "缺少 group_returns"
assert len(lb["group_returns"]) == 5, f"应返回5组: {len(lb['group_returns'])}"
assert isinstance(lb["is_monotonic"], bool), "is_monotonic应为bool"
assert lb["is_monotonic"], "因子→收益正相关，分层收益应单调"
assert lb["spread"] > 0, f"spread应>0: {lb['spread']:.4f}"

close_a2 = pd.Series(100 + np.cumsum(np.random.randn(n) * 0.5))
close_b = pd.Series(100 + np.cumsum(np.random.randn(n) * 0.5))
close_data = {
    "A": pd.DataFrame({"close": close_a2}),
    "B": pd.DataFrame({"close": close_b}),
}

def _calc_roc(close, period):
    return close / close.shift(period) - 1

ic_m, icir_m = FactorLab.ic_matrix_scan(_calc_roc, close_data, [5, 10, 22], [5, 10])
print()
print("=== ic_matrix_scan ===")
print("IC matrix:")
print(ic_m)
print()
print("ICIR matrix:")
print(icir_m)

assert not ic_m.empty, "IC矩阵不能为空"
assert not icir_m.empty, "ICIR矩阵不能为空"
assert ic_m.shape == (3, 2), f"IC矩阵维度应为(3,2): {ic_m.shape}"
assert icir_m.shape == (3, 2), f"ICIR矩阵维度应为(3,2): {icir_m.shape}"

print()
print("ALL TESTS PASSED")
