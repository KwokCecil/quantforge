"""
特性墓地 —— ROCStrategy 已证伪的策略增强代码。

这些代码原本是 ROCStrategy 的方法，经严格回测验证全周期无正向贡献。
于 2026-05-30 (T048) 从 roc_momentum.py 移出至此。不导入、不运行、不维护。

目录结构：
  voting.py         — 多指标投票增强（10个方法）
  rsi_factor.py     — RSI 单因子替代（2个方法）
  residual.py       — 残差动量（2个方法）
  multifactor.py    — 多周期ROC多因子（1个方法）
  ts_and_crash.py   — 时序动量 + 崩盘防护（inline代码 + 1个方法）

如需恢复某特性，回调 roc_momentum.py 并添加对应的 config flag。
详见：T048_P_证伪代码清理_ROCStrategy特性墓地.md
"""