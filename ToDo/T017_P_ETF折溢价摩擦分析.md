# T017 ETF折溢价摩擦分析

> **状态**：pending
> **所属阶段**：Phase 4 — 系统完善（摩擦分析）
> **并行安全**：是（纯新建文件，不改已有模块）
> **占用文件**：research/friction_analyzer.py（新建）
> **创建日期**：2026-05-05
> **完成日期**：—

## 一、前置依赖

- **T005 ROC 多因子增强 + T004 股债利差**：已有交易日志可供分析
- 无硬性代码依赖

## 二、目标

量化 ETF 折溢价对策略收益的侵蚀效应。当前回测中完全忽略了 ETF 的一级/二级市场价差（溢价买入 = 隐性成本，折价卖出 = 隐性亏损），QDII ETF 溢价可达 5%+。

**产出**：`research/friction_analyzer.py`，核心函数 `estimate_premium_impact(trade_log, ohlc_data) → dict`，输出"每笔交易的预估溢价成本"和"总成本占比"。

## 三、实施计划

### Step 1：定义摩擦分析的数据结构

```python
@dataclass
class FrictionReport:
    """ETF 摩擦分析报告。"""
    strategy_name: str
    total_trades: int
    total_premium_cost: float           # 溢价买入的预估总成本
    total_discount_loss: float          # 折价卖出的预估总损失
    total_friction: float               # 总摩擦成本
    friction_ratio: float               # 摩擦占总收益的百分比
    avg_premium_per_trade: float        # 平均每笔溢价
    worst_trade: dict                   # 最差单笔交易
    qdii_premium_warning: bool          # QDII 高溢价警告
```

### Step 2：实现折溢价估算逻辑

由于回测中无真实折溢价数据，采用**保守估计**：

| ETF 类型 | 预估买入溢价 | 预估卖出折价 | 理由 |
|---------|------------|------------|------|
| A 股 ETF | 0.05% | 0.05% | IOPV 偏离小，做市商充分 |
| QDII ETF | 1.0%~3.0% | 1.0%~3.0% | 跨境申赎限制，溢价常见 |
| 债券 ETF | 0.1% | 0.1% | 流动性好，偏离小 |

```python
PREMIUM_ESTIMATES = {
    "A_STOCK": {"buy": 0.0005, "sell": 0.0005},
    "QDII":    {"buy": 0.015,  "sell": 0.015},
    "BOND":    {"buy": 0.001,  "sell": 0.001},
}
```

### Step 3：实现 `FrictionAnalyzer` 类

```python
class FrictionAnalyzer:
    def __init__(self, trade_log: list[dict], ohlc_data: dict, etf_classifications: dict):
        ...

    def analyze(self) -> FrictionReport:
        """逐笔计算预估溢价成本，汇总为报告。"""
        ...

    def estimate_premium_impact(self, trade: dict) -> float:
        """单笔交易的预估溢价/折价影响。"""
        code = trade['code']
        etf_type = self._classify(code)
        if trade['direction'] == 'buy':
            return trade['amount'] * PREMIUM_ESTIMATES[etf_type]['buy']
        else:
            return trade['amount'] * PREMIUM_ESTIMATES[etf_type]['sell']

    def compare_strategies(self, reports: list[FrictionReport]) -> dict:
        """多策略摩擦对比。"""
        ...
```

### Step 4：对现有策略跑摩擦分析

用 T004（股债利差）和 T005（ROC 动量）的交易日志跑一遍，产出摩擦分析报告：
- 摩擦成本占总收益的百分比
- QDII ETF 是否显著增加了摩擦
- 哪些标的在哪些时期溢价/折价可能最大

## 四、验收标准

- [ ] `FrictionAnalyzer` 能正确分类 ETF（A股/QDII/债券）
- [ ] 预估溢价逻辑覆盖率覆盖所有交易类型
- [ ] 输出 FrictionReport 含"总摩擦/总收益"比率
- [ ] 对 T004 和 T005 策略产出摩擦报告
- [ ] QDII 标的的高溢价警告正确触发

## 五、相关文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `research/friction_analyzer.py` | **新建** | 摩擦分析核心实现 |
| `research/__init__.py` | 可能修改 | 导出 FrictionAnalyzer |

## 六、备注

- **当前阶段只做估计，不做实时监控**：实盘监控中的折溢价数据需要行情源支持（如实时 IOPV），这是 L3.5+ 的内容
- **QDII 溢价的季节性**：QDII ETF 在额度紧张时溢价飙升（如 2024 年日经 ETF 溢价一度达 10%+），保守估计可能低估真实成本
- **摩擦分析的结论可能只是"知道"，而非"改变"**：低换手策略（T004 年化 2 次交易）摩擦天然低；高换手策略（ROC 年均 15-20 次）摩擦更值得关注
- **与 T018 分钟K线的关系**：日内策略换手更高，摩擦分析对日内策略更重要
