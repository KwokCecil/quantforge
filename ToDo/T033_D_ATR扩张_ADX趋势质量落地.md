# T033 ATR扩张 + ADX趋势质量落地

> **状态**：completed
> **所属阶段**：Phase 4 — 系统完善
> **前置依赖**：T025（策略开关全周期验证），T028（买入信号过滤）
> **并行安全**：是（新增 Indicator + 配置字段 + _evaluate 过滤，不改现有逻辑）
> **创建日期**：2026-05-19
> **完成日期**：2026-05-19

## 一、前置依赖

- T025 全周期回测验证：ATR波动率扩张 + ADX趋势质量 贡献 +2.8pp 年化
- 当前 `atr_filter_enabled` 是 T028 出产的"正常波不买"（ATR 25~75分位），与此不同
- `ADXIndicator` 类尚未实现

## 二、目标

落地 T025 验证的 ATR 扩张 + ADX 趋势双层择时，作为独立的买入过滤开关。

### ATR波动率扩张

```
ATR(20) > 1.3 × ATR(200) → 禁止新买入
```

逻辑：当前波动率显著高于长期均值，说明出现极端行情，不应追入。
T025 案例：2020年武汉封城后17天即触发ATR扩张信号。

### ADX趋势质量

```
ADX(14) < 20 → 禁止新买入（震荡市不参与）
```

逻辑：ADX（平均趋向指数）衡量趋势强度。ADX<20 表示无明显趋势（震荡/磨底），ROC动量信号质量差。
T025 案例：过滤2021-2023漫长磨底中的假反弹。

### 两者可独立开关

| Config 字段 | 默认 | 含义 |
|-------------|------|------|
| `atr_expansion_filter_enabled` | false | ATR(20) > 1.3×ATR(200) 时禁止买入 |
| `adx_trend_filter_enabled` | false | ADX(14) < 20 时禁止买入 |

## 三、实施计划

### Step 1：实现 ADXIndicator

在 `indicators/technical.py` 新增：

```python
class ADXIndicator(Indicator):
    """ADX平均趋向指数。+DI/-DI/ADX。ADX = MA(DX, n)"""
    def __init__(self, n: int = 14): ...
    def compute(self, data, **kwargs): ...
```

公式：`TR = max(H-L, |H-REF(C,1)|, |L-REF(C,1)|)`，
`+DM = H-REF(H,1) if >0 and > L-REF(L,1) else 0`，
`-DM = REF(L,1)-L if >0 and > H-REF(H,1) else 0`，
`+DI = MA(+DM, n) / ATR × 100`，`-DI = MA(-DM, n) / ATR × 100`，
`DX = |(+DI) - (-DI)| / ((+DI) + (-DI)) × 100`，`ADX = MA(DX, n)`

### Step 2：ROCConfig 新增字段

→ `atr_expansion_filter_enabled: bool = False`
→ `adx_trend_filter_enabled: bool = False`

### Step 3：ROCStrategy 整合

- `__init__`：条件创建 ATRIndicator(n=200) + ADXIndicator(n=14)
- `_produce_singlefactor_decisions`：计算 atr20/atr200/ADX，写入 `indicator_data`
- `_evaluate`：ATR扩张 → hold；ADX<20 → hold

### Step 4：测试

- `tests/_test_adx_indicator_unit.py`：ADXIndicator 合成数据验证（上涨趋势ADX>20、震荡ADX<20）
- `tests/_test_atr_expansion_filter_unit.py`：ATR扩张检测（ATR20>1.3×ATR200/正常/不足数据）
- 全量测试确认无回归

## 四、验收标准

- [x] ADXIndicator 单元测试 6 项通过
- [x] ATR扩张 + ADX过滤 单元测试 8 项通过
- [x] 全量 `run_all_tests.py` 全部 PASS (16/16)
- [x] `max_attack + ATR+ADX` 回测：+80.19% / Sharpe 0.53 / DD 33.54% (vs 纯max_attack +74.80%)

## 五、相关文件

| 文件 | 类型 | 说明 |
|------|------|------|
| `indicators/technical.py` | 修改 | 新增 ADXIndicator |
| `strategies/_configs/roc_config.py` | 修改 | 新增 2 个配置字段 |
| `strategies/roc_momentum.py` | 修改 | 集成 ATR扩张+ADX过滤 |
| `tests/_test_adx_indicator_unit.py` | 新建 | ADXIndicator 单元测试 |
| `tests/_test_atr_expansion_filter_unit.py` | 新建 | ATR扩张+ADX过滤测试 |
| `指导文档/5.08_策略开关有效性研究总表.md` | 修改 | 标记 🟡→🟢 |

## 六、备注

- 与现有 `atr_filter_enabled`（T028正常波不买）互不冲突，可共存
- ADX 公式参考 Wilder (1978) 《New Concepts in Technical Trading Systems》
- 数据需求：ATR计算需要 200 天历史，ADX需要 ~28 天（14 + 14 Wilder平滑）
