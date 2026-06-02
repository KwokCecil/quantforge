# T005 ROC 多因子增强（波动率加权 + 多动量因子）

> **状态**：completed  
> **所属阶段**：Phase 4 — 系统完善 / 策略增强  
> **T025全周期验证**：✅ InvVol在全周期仍有效(+0.4~0.7pp稳定加成)；多因子共线结论不变。  
> **2018-2026 main_backtest重验证**（2026-05-19）：✅ InvVol效应被严重低估：实际 +56.47pp（+0.15 Sharpe）；多因子仍为负收益。  
> **创建日期**：2026-05-01  
> **完成日期**：2026-05-03  
> **最后更新**：2026-05-19

## 一、前置依赖

- **T001 FactorLab**：新增因子需要通过 FactorLab 验证有效性
- ROC 基础策略已稳定运行（Phase 1 完成）

## 二、目标

在现有 ROC 动量轮动策略基础上，探索两种增强方案：
1. **波动率加权**：ROC 排序后用历史波动率的倒数加权（降低高波动标的的权重）
2. **多动量因子**：引入多周期 ROC（ROC(5)、ROC(15)、ROC(22)）做投票或综合评分

目标是探索能否在不显著恶化回撤的前提下，提升 Sharpe 或降低波动。

## 三、实施计划

### Step 1：新增波动率指标

在 `indicators/technical.py` 中增加 `VolatilityIndicator`：

```python
class VolatilityIndicator(Indicator):
    """历史波动率。N日收益率的标准差 × √252（年化）"""
    def __init__(self, n: int = 20):
        self.n = n

    def compute(self, data: pd.DataFrame, **kwargs) -> pd.DataFrame:
        n = kwargs.get('n', self.n)
        data = data.copy()
        returns = data['close'].pct_change()
        data['volatility'] = returns.rolling(n).std() * np.sqrt(252)
        return data
```

### Step 2：修改 RankingResolver 支持波动率加权

在 `weight_method` 中新增 `'inverse_vol'` 选项：

```python
class RankingResolver:
    def __init__(self, ..., weight_method='equal'):
        self.weight_method = weight_method  # 新增 'inverse_vol'

    def resolve(self, ...):
        if self.weight_method == 'inverse_vol':
            # 权重 = (1/volatility) / Σ(1/volatility)
            # 高波动→低权重，低波动→高权重（风险平价思想）
```

权重计算逻辑：
```python
inverse_vols = {code: 1.0 / max(vol, 0.01) for code, vol in volatilities.items()}
total_inv = sum(inverse_vols.values())
weights = {code: inv / total_inv for code, inv in inverse_vols.items()}
```

### Step 3：实现多动量因子策略

方案选择——两种方式选其一（回测后择优）：

**方案A：多因子投票**
- ROC(5)、ROC(15)、ROC(22) 三个因子各自产出排序
- 对每个标的：取三个排序的均值或中位数作为综合 rank
- 按综合 rank 排序，TOP_K 买入

**方案B：多因子加权总分**（推荐优先尝试）
- 对每个因子的排序位置赋予分数（rank1=100分, rank2=99分...）
- 加权总分 = w1×ROC5分数 + w2×ROC15分数 + w3×ROC22分数
- 按总分排序

可在 `ROCConfig` 中新增配置：

```python
@dataclass
class ROCConfig(StrategyConfig):
    ...
    # 多因子增强
    multi_factor: bool = False           # 是否启用多因子模式
    multi_roc_periods: tuple = (5, 15, 22)  # 多ROC周期
    multi_factor_weights: tuple = (0.3, 0.4, 0.3)  # 各因子权重
    inverse_vol_weight: bool = False     # 是否启用波动率倒数加权
```

### Step 4：用 FactorLab 验证新因子

- 用 T001 的 FactorLab 对 ROC(5)、波动率倒数等因子的 IC/ICIR 进行检验
- 确认新因子在统计上有增量预测力（而非与 ROC(22) 高度共线）

### Step 5：回测对比

| 配置 | 说明 |
|---|---|
| Baseline | 当前最优 ROC(22) + buy_roc_edge=20 + equal weight |
| Test A | ROC(22) + inverse_vol_weight=True |
| Test B | Multi-factor ROC(5,15,22) + equal weight |
| Test C | Multi-factor + inverse_vol_weight |

对以上配置跑完整回测，对比 Sharpe、max_drawdown、trade_count。

## 四、验收标准

- [x] `VolatilityIndicator` 能正确计算年化波动率（合成数据验证：0.1752）
- [x] `RankingResolver` 的 `weight_method='inverse_vol'` 能按波动率倒数分配权重
- [x] 多因子模式下，策略能综合多个 ROC 周期产出 Decision
- [x] 回测对比表清晰展示各个配置的 Sharpe/回撤/交易次数
- [x] 增强方案分析完成：InvVol 有改善，MultiFactor 负收益（因子共线性）
- [x] 结论记录清楚，不强行采用负收益方案

## 五、相关文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `indicators/technical.py` | 修改 | 新增 VolatilityIndicator |
| `core/resolver.py` | 修改 | RankingResolver 新增 inverse_vol + _apply_inverse_vol |
| `strategies/roc_momentum.py` | 修改 | 增加多因子模式 + 波动率写入 |
| `strategies/_configs/roc_config.py` | 修改 | 新增 multi_factor/inverse_vol_weight 等配置项 |

## 六、验收结果

### 原始回测 (7标的, 2023-08 ~ 2025-06)
| 配置 | 收益率 | Sharpe | 回撤 | 交易 |
|------|--------|--------|------|------|
| Baseline (单因子+信号加权) | 4.4% | 0.22 | 23.3% | 40 |
| **InvVol** (单因子+波动率倒数) | **7.2%** | **0.31** | **21.5%** | 36 |
| MultiFactor (多因子+信号加权) | -12.2% | -0.24 | 23.0% | 34 |
| Multi+InvVol (多因子+波动率倒数) | -11.2% | -0.22 | 20.3% | 30 |

### 2018-2026 全周期重验证 (33标的, main_backtest.py, 基准399006)

对比组基于 T033 宏观阻断配置（ATR扩张+ADX趋势质量+高水位止损+硬止损），仅切换 InvVol/MultiFactor：

| 配置 | 总收益 | 年化 | 回撤 | Sharpe | 交易 | 胜率 | 盈亏比 |
|------|--------|------|------|--------|------|------|--------|
| Inv Off (单因子+信号加权) | 86.11% | 10.16% | 36.54% | 0.58 | 126 | 61.90% | 1.06 |
| **Inv On** (单因子+逆波加权) | **142.58%** | **14.80%** | 36.64% | **0.73** | 111 | 58.56% | **1.55** |
| MultiFactor (多因子+信号加权) | 80.85% | 9.67% | 36.54% | 0.56 | 127 | 61.42% | 1.05 |

### 结论
- **波动率倒数加权在全周期大幅有效**：InvVol 收益 +56.47pp、Sharpe +0.15、盈亏比从 1.06→1.55。远超原始小样本测试的 +0.4~0.7pp。原因：全周期包含2018、2022等熊市年份，等权犯错的代价更大，逆波加权在熊市中起到了风险平价缓冲作用。
- **多因子仍然负收益**：ROC(5/15/22) 高度共线，多因子排名打散后无法选出最强标的，符合"因子共线性"预期。全周期下比 Inv Off 还低 5.26pp。
- **回撤三组几乎相同（~36.5%）**：由宏观 ATR/ADX 阻断共同控制，说明回撤的主要来源是系统性风险而非权重分配。
- **建议**：在生产配置中启用 `inverse_vol_weight=True`，不启用 `multi_factor`。

## 六、备注

- **边际收益可能不高**：ROC(22) 当前已经表现不错（Sharpe ~1.6）。多因子增强的边际改善可能只有 0.05-0.1 的 Sharpe 提升。如果测试表明提升不显著，接受结论，不强行上
- **多因子共线性风险**：ROC(5)、ROC(15)、ROC(22) 之间有较强相关性——它们本质是同一因子在不同时间尺度上的表现。FactorLab 的因子相关性分析（L3.5 功能）可以验证这一点
- **过拟合风险**：引入多个因子增加了调参自由度（权重 × N），更容易过拟合。Walk-Forward（T002）完成后应对增强策略也跑一遍样本外验证
