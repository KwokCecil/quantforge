# @layer: unit
import pandas as pd
import numpy as np
from quantforge.indicators.technical import RSIIndicator, MACDIndicator

np.random.seed(42)
n = 200
close = 100 + np.cumsum(np.random.randn(n) * 0.5)

df = pd.DataFrame({'close': close, 'high': close * 1.02, 'low': close * 0.98})

rsi_ind = RSIIndicator(n=14)
result_rsi = rsi_ind.compute(df)
rsi_vals = result_rsi['rsi'].dropna()
print(f'RSI: count={len(rsi_vals)}, min={rsi_vals.min():.1f}, max={rsi_vals.max():.1f}, mean={rsi_vals.mean():.1f}')
assert 0 <= rsi_vals.min() <= 100, 'RSI out of range'
assert 0 <= rsi_vals.max() <= 100, 'RSI out of range'

macd_ind = MACDIndicator(fast=12, slow=26, signal=9)
result_macd = macd_ind.compute(df)
print(f'MACD DIF: min={result_macd["dif"].min():.3f}, max={result_macd["dif"].max():.3f}')
print(f'MACD DEA: min={result_macd["dea"].min():.3f}, max={result_macd["dea"].max():.3f}')
dif = result_macd['dif'].values
dea = result_macd['dea'].values
cross_count = sum(1 for i in range(1, len(dif)) if (dif[i-1] <= dea[i-1] and dif[i] > dea[i]))
print(f'MACD golden cross count: {cross_count}')
assert 'macd_bar' in result_macd.columns
print('All checks passed!')
