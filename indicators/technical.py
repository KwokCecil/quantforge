import numpy as np
import pandas as pd

from quantforge.core.indicator import Indicator


# ---- 通用技术指标辅助函数（内联实现，不强制依赖MyTT库） ----

def MA(S, N):
    """简单移动平均"""
    return pd.Series(S).rolling(N).mean().values


def REF(S, N=1):
    """引用N周期前的值"""
    return pd.Series(S).shift(N).values


def STD(S, N):
    """N周期标准差"""
    return pd.Series(S).rolling(N).std().values


def MAX(A, B):
    """逐元素取最大值"""
    return np.maximum(A, B)


def ABS(S):
    """逐元素取绝对值"""
    return np.abs(S)


class ROCIndicator(Indicator):
    """变动率指标。ROC = 100 * (C - REF(C,N)) / REF(C,N)，MAROC = MA(ROC, M)"""
    def __init__(self, n: int = 12, m: int = 6):
        self.n = n
        self.m = m

    def compute(self, data: pd.DataFrame, **kwargs) -> pd.DataFrame:
        n = kwargs.get('n', self.n)
        m = kwargs.get('m', self.m)
        close = data['close'].values.astype(float)
        ref_close = REF(close, n)
        with np.errstate(divide='ignore', invalid='ignore'):
            roc = np.where(ref_close != 0, 100 * (close - ref_close) / ref_close, np.nan)
        maroc = MA(roc, m)
        data = data.copy()
        data['roc'] = roc
        data['maroc'] = maroc
        return data


class MAIndicator(Indicator):
    """多周期移动平均线指标"""
    def __init__(self, periods: list[int] | None = None):
        self.periods = periods or [5, 10, 20, 60]

    def compute(self, data: pd.DataFrame, **kwargs) -> pd.DataFrame:
        periods = kwargs.get('periods', self.periods)
        data = data.copy()
        close = data['close'].values
        for p in periods:
            data[f'ma{p}'] = MA(close, p)
        return data


class BOLLIndicator(Indicator):
    """布林带指标。MID = MA(C,N), UPPER = MID + P*STD, LOWER = MID - P*STD"""
    def __init__(self, n: int = 20, p: int = 2):
        self.n = n
        self.p = p

    def compute(self, data: pd.DataFrame, **kwargs) -> pd.DataFrame:
        n = kwargs.get('n', self.n)
        p = kwargs.get('p', self.p)
        data = data.copy()
        close = data['close'].values
        mid = MA(close, n)
        upper = mid + p * STD(close, n)
        lower = mid - p * STD(close, n)
        data['boll_mid'] = mid
        data['boll_upper'] = upper
        data['boll_lower'] = lower
        data['b_pct'] = (data['close'].values - lower) / (upper - lower)
        return data


class ATRIndicator(Indicator):
    """真实波幅均值指标。TR = max(H-L, |REF(C,1)-H|, |REF(C,1)-L|), ATR = MA(TR, N)"""
    def __init__(self, n: int = 20):
        self.n = n

    def compute(self, data: pd.DataFrame, **kwargs) -> pd.DataFrame:
        n = kwargs.get('n', self.n)
        data = data.copy()
        high = data['high'].values
        low = data['low'].values
        close = data['close'].values
        tr = MAX(MAX(high - low, ABS(REF(close, 1) - high)), ABS(REF(close, 1) - low))
        data['atr'] = MA(tr, n)
        return data


class VolatilityIndicator(Indicator):
    """历史波动率指标。N日收益率标准差 × √252（年化）。高波动→低权重（风险平价思想）"""
    def __init__(self, n: int = 20):
        self.n = n

    def compute(self, data: pd.DataFrame, **kwargs) -> pd.DataFrame:
        n = kwargs.get('n', self.n)
        data = data.copy()
        returns = data['close'].pct_change()
        data['volatility'] = returns.rolling(n).std() * np.sqrt(252)
        return data


class RSIIndicator(Indicator):
    """相对强弱指标（Wilder's RSI）。RSI = 100 - 100/(1 + RS)，RS = WilderAvgGain / WilderAvgLoss。
    使用Wilder平滑（alpha=1/N），与标准EMA（alpha=2/(N+1)）不同。
    值为0表示连续全跌（无上涨日），值为100表示连续全涨（无下跌日）。
    """
    def __init__(self, n: int = 14):
        self.n = n

    def compute(self, data: pd.DataFrame, **kwargs) -> pd.DataFrame:
        n = kwargs.get('n', self.n)
        data = data.copy()
        close = data['close'].values.astype(float)
        delta = np.diff(close, prepend=close[0])
        gain = np.where(delta > 0, delta, 0.0)
        loss = np.where(delta < 0, -delta, 0.0)
        avg_gain = pd.Series(gain).ewm(alpha=1.0 / n, adjust=False).mean().values
        avg_loss = pd.Series(loss).ewm(alpha=1.0 / n, adjust=False).mean().values
        with np.errstate(divide='ignore', invalid='ignore'):
            rs = np.where(avg_loss != 0, avg_gain / avg_loss, np.inf)
            rsi = np.where(avg_loss != 0, 100.0 - 100.0 / (1.0 + rs), 100.0)
        data['rsi'] = rsi
        return data


class MACDIndicator(Indicator):
    """指数平滑异同移动平均线。DIF = EMA(C,fast) - EMA(C,slow)，DEA = EMA(DIF,signal)。
    MACD柱(histogram) = 2 × (DIF - DEA)，正值表示DIF在DEA之上（多头）。
    """
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def compute(self, data: pd.DataFrame, **kwargs) -> pd.DataFrame:
        fast = kwargs.get('fast', self.fast)
        slow = kwargs.get('slow', self.slow)
        signal = kwargs.get('signal', self.signal)
        data = data.copy()
        close = pd.Series(data['close'].values.astype(float))
        ema_fast = close.ewm(span=fast, adjust=False).mean().values
        ema_slow = close.ewm(span=slow, adjust=False).mean().values
        dif = ema_fast - ema_slow
        dea = pd.Series(dif).ewm(span=signal, adjust=False).mean().values
        macd_bar = 2.0 * (dif - dea)
        data['dif'] = dif
        data['dea'] = dea
        data['macd_bar'] = macd_bar
        return data


class ADXIndicator(Indicator):
    """平均趋向指数（Average Directional Index）。
    +DI/-DI/ADX。ADX > 20 表示趋势明显，< 20 表示震荡。
    Wilder (1978) 《New Concepts in Technical Trading Systems》。
    """
    def __init__(self, n: int = 14):
        self.n = n

    def compute(self, data: pd.DataFrame, **kwargs) -> pd.DataFrame:
        n = kwargs.get('n', self.n)
        data = data.copy()
        high = data['high'].values.astype(float)
        low = data['low'].values.astype(float)
        close = data['close'].values.astype(float)

        tr = MAX(MAX(high - low, ABS(REF(close, 1) - high)), ABS(REF(close, 1) - low))

        pdm_raw = high - REF(high, 1)
        ndm_raw = REF(low, 1) - low
        pdm = np.where((pdm_raw > 0) & (pdm_raw > ndm_raw), pdm_raw, 0.0)
        ndm = np.where((ndm_raw > 0) & (ndm_raw > pdm_raw), ndm_raw, 0.0)

        atr_wilder = pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().values
        apdm = pd.Series(pdm).ewm(alpha=1.0 / n, adjust=False).mean().values
        andm = pd.Series(ndm).ewm(alpha=1.0 / n, adjust=False).mean().values

        with np.errstate(divide='ignore', invalid='ignore'):
            pdi = np.where(atr_wilder != 0, 100.0 * apdm / atr_wilder, 0.0)
            ndi = np.where(atr_wilder != 0, 100.0 * andm / atr_wilder, 0.0)
            dx = np.where((pdi + ndi) != 0, 100.0 * np.abs(pdi - ndi) / (pdi + ndi), 0.0)

        adx = pd.Series(dx).ewm(alpha=1.0 / n, adjust=False).mean().values

        data['pdi'] = pdi
        data['mdi'] = ndi
        data['adx'] = adx
        return data
