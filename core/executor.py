import json
import os
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from quantforge.core.data_feed import DataResponse
from quantforge.core.resolver import TargetPosition


class Executor(ABC):
    """执行层抽象接口。回测用BacktestExecutor(模拟撮合)，实盘用LiveExecutor(推送通知)。"""
    @abstractmethod
    def execute(self, targets: list[TargetPosition], data: DataResponse) -> dict:
        pass

    @abstractmethod
    def get_positions(self) -> dict[str, Any]:
        pass


class BacktestExecutor(Executor):
    """回测执行器——模拟撮合，含交易成本建模。

    核心设计：
    - 根据TargetPosition的target_weight与当前持仓的差距决定买卖
    - 买入价 = close * (1 + slippage)，卖出价 = close * (1 - slippage)
    - 佣金 = max(成交额 * commission_rate, min_commission)，默认免5（min_commission=0）
    - A股规则：买入份额必须为100的整数倍
    - 追加买入时加权平均成本，每日更新高水位线
    - 部分卖出时保留剩余持仓
    """
    def __init__(self, initial_capital: float = 40000.0,
                 commission_rate: float = 0.00025,
                 min_commission: float = 0.0,
                 slippage: float = 0.001,
                 rebalance: bool = False,
                 stop_small_trade: bool = True,
                 skip_small_trade_limit: float = 2000.0):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.commission_rate = commission_rate
        self.min_commission = min_commission
        self.slippage = slippage
        self.rebalance = rebalance
        self._config_stop_small = stop_small_trade
        self._config_small_limit = skip_small_trade_limit
        self.positions: dict[str, dict] = {}   # {code: {'shares': int, 'avg_cost': float, 'high_watermark': float}}
        self.trade_log: list[dict] = []
        self.net_values: list[dict] = []
        self.total_commission = 0.0
        self.total_slippage = 0.0

    def execute(self, targets: list[TargetPosition], data: DataResponse) -> dict:
        current_date = self._get_current_date(data)

        sell_targets = [t for t in targets if t.target_weight == 0.0 and t.code in self.positions]
        raw_buy_targets = [t for t in targets if t.target_weight > 0.0]

        for target in sell_targets:
            code = target.code
            if code not in self.positions or code not in data.bar_data or data.bar_data[code].empty:
                continue
            close_price = float(data.bar_data[code].iloc[-1]['close'])
            if close_price <= 0:
                continue
            current_shares = self.positions.get(code, {}).get('shares', 0)
            self._sell(code, current_shares, close_price, target.reason, current_date)

        # 调仓减仓: 0 < target_weight < 1 且已持有 → 只卖出超额部分
        total_value = self._compute_total_value(data)
        rebalanced_codes = set()
        for target in raw_buy_targets:
            code = target.code
            if code not in self.positions or target.target_weight >= 1.0:
                continue
            if code not in data.bar_data or data.bar_data[code].empty:
                continue
            close_price = float(data.bar_data[code].iloc[-1]['close'])
            if close_price <= 0:
                continue
            current_shares = self.positions[code]['shares']
            if current_shares <= 0:
                continue
            current_value = current_shares * close_price
            target_value = total_value * target.target_weight
            if current_value <= target_value * 1.01:
                continue
            excess_value = current_value - target_value
            shares_to_sell = int(excess_value / (close_price * (1 + self.slippage)) / 100) * 100
            if shares_to_sell > 0:
                self._sell(code, shares_to_sell, close_price,
                          f"{target.reason} → 调仓减至{target.target_weight:.0%}", current_date)
                rebalanced_codes.add(code)

        buy_targets = [t for t in raw_buy_targets if t.code not in rebalanced_codes]

        if buy_targets:
            total_weight = sum(t.target_weight for t in buy_targets)
            available_cash = self.cash
            total_value = self._compute_total_value(data)
            for target in buy_targets:
                code = target.code
                if code not in data.bar_data or data.bar_data[code].empty:
                    continue
                df = data.bar_data[code]
                close_price = float(df.iloc[-1]['close'])
                if close_price <= 0:
                    continue

                if total_weight > 0:
                    money_available = available_cash * (target.target_weight / total_weight)
                else:
                    money_available = available_cash / len(buy_targets)

                current_shares = self.positions.get(code, {}).get('shares', 0)

                if current_shares > 0:
                    current_value = current_shares * close_price
                    target_value = money_available + current_value
                    target_shares = int(target_value / (close_price * (1 + self.slippage)) / 100) * 100
                    shares = max(target_shares - current_shares, 0)
                    shares = shares // 100 * 100
                else:
                    shares = int(money_available / (close_price * (1 + self.slippage)) / 100) * 100

                if self._config_stop_small and abs(shares * close_price) < self._config_small_limit:
                    continue

                if shares > 0:
                    self._buy(code, shares, close_price, target.reason, current_date)

        self._update_high_watermarks(data)
        total_value = self._compute_total_value(data)
        self.net_values.append({
            'date': current_date,
            'net_value': total_value / self.initial_capital,
            'cash': self.cash,
            'total_value': total_value,
        })

        return {'total_value': total_value}

    def get_positions(self) -> dict[str, Any]:
        return self.positions

    def available_capital(self) -> float:
        return self.cash

    def get_results(self) -> dict:
        return {
            'initial_capital': self.initial_capital,
            'final_value': self._compute_total_value_from_last_net_value(),
            'positions': self.positions,
            'trade_log': self.trade_log,
            'net_values': self.net_values,
            'total_commission': self.total_commission,
            'total_slippage': self.total_slippage,
        }

    def _buy(self, code: str, shares: int, price: float, reason: str, trade_date: str = ""):
        actual_price = price * (1 + self.slippage)
        trade_value = shares * actual_price
        commission = max(trade_value * self.commission_rate, self.min_commission)
        total_cost = trade_value + commission

        # 资金不足时按可用资金调整份额（向下取整到100的倍数）
        if total_cost > self.cash:
            shares = int((self.cash - self.min_commission) / (actual_price * (1 + self.commission_rate)) / 100) * 100
            if shares <= 0:
                return
            trade_value = shares * actual_price
            commission = max(trade_value * self.commission_rate, self.min_commission)
            total_cost = trade_value + commission

        self.cash -= total_cost
        self.total_commission += commission
        self.total_slippage += shares * price * self.slippage

        if code in self.positions:
            # 追加买入：加权平均成本
            old = self.positions[code]
            total_shares = old['shares'] + shares
            new_avg = (old['shares'] * old['avg_cost'] + shares * actual_price) / total_shares
            self.positions[code] = {
                'shares': total_shares,
                'avg_cost': new_avg,
                'high_watermark': max(old['high_watermark'], actual_price),
            }
        else:
            self.positions[code] = {
                'shares': shares,
                'avg_cost': actual_price,
                'high_watermark': actual_price,
            }

        self.trade_log.append({
            'action': 'buy',
            'date': trade_date,
            'code': code,
            'shares': shares,
            'price': price,
            'actual_price': actual_price,
            'commission': commission,
            'reason': reason,
            'timestamp': datetime.now().isoformat(),
        })

    def _sell(self, code: str, shares: int, price: float, reason: str, trade_date: str = ""):
        if code not in self.positions:
            return

        pos = self.positions[code]
        if shares > pos['shares']:
            shares = pos['shares']

        actual_price = price * (1 - self.slippage)
        trade_value = shares * actual_price
        commission = max(trade_value * self.commission_rate, self.min_commission)

        self.cash += trade_value - commission
        self.total_commission += commission
        self.total_slippage += shares * price * self.slippage

        # 全部卖出→删除持仓；部分卖出→减少份额
        if shares >= pos['shares']:
            del self.positions[code]
        else:
            pos['shares'] -= shares

        self.trade_log.append({
            'action': 'sell',
            'date': trade_date,
            'code': code,
            'shares': shares,
            'price': price,
            'actual_price': actual_price,
            'commission': commission,
            'reason': reason,
            'timestamp': datetime.now().isoformat(),
        })

    def _update_high_watermarks(self, data: DataResponse):
        """每日更新持仓的高水位线，用于高水位止损判断。"""
        for code, pos in self.positions.items():
            if code in data.bar_data and not data.bar_data[code].empty:
                current_price = float(data.bar_data[code].iloc[-1]['close'])
                pos['high_watermark'] = max(pos['high_watermark'], current_price)

    def _compute_total_value(self, data: DataResponse) -> float:
        """计算总资产 = 现金 + 持仓市值（按最新收盘价计算）。"""
        total = self.cash
        for code, pos in self.positions.items():
            if code in data.bar_data and not data.bar_data[code].empty:
                current_price = float(data.bar_data[code].iloc[-1]['close'])
                total += pos['shares'] * current_price
            else:
                total += pos['shares'] * pos['avg_cost']
        return total

    def _compute_total_value_from_last_net_value(self) -> float:
        if self.net_values:
            return self.net_values[-1]['total_value']
        return self.initial_capital

    def _get_current_date(self, data: DataResponse) -> str:
        for code, df in data.bar_data.items():
            if not df.empty and 'date' in df.columns:
                return str(df.iloc[-1]['date'])
        return datetime.now().strftime('%Y-%m-%d')


class LiveExecutor(Executor):
    """实盘执行器——不自动下单，生成可操作的交易建议推送通知。

    每日收盘前执行一次，基于当前价格计算股数和金额。
    持仓持久化到JSON文件，含成本和高水位线。
    首次使用需手动创建position/position.json。
    """
    def __init__(self, notifier=None, position_file: str = 'position/position.json',
                 initial_capital: float = 40000.0,
                 commission_rate: float = 0.00025,
                 slippage: float = 0.001,
                 code_names: dict[str, str] | None = None,
                 name: str = "",
                 dry_run: bool = False):
        self.notifier = notifier
        self.position_file = position_file
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage = slippage
        self.min_commission = 0.0
        self.code_names = code_names or {}
        self.name = name
        self.dry_run = dry_run
        self.positions = self._load_positions()

    def execute(self, targets: list[TargetPosition], data: DataResponse) -> dict:
        today = datetime.now().strftime('%Y-%m-%d')

        sell_targets = [t for t in targets if t.target_weight == 0.0 and t.code in self._holding_codes()]
        buy_targets = [t for t in targets if t.target_weight > 0.0]

        free_cash = self.positions.get('free_capital', 0.0)
        messages = []
        released_cash = 0.0

        for target in sell_targets:
            code = target.code
            pos = self.positions[code]
            shares = pos['shares']
            price = self._get_price(code, data)
            if price <= 0:
                continue

            actual_price = price * (1 - self.slippage)
            trade_value = shares * actual_price
            commission = max(trade_value * self.commission_rate, 0.0)
            net_cash = trade_value - commission
            released_cash += net_cash

            pnl = (actual_price - pos['avg_cost']) * shares - commission
            pnl_pct = pnl / (pos['avg_cost'] * shares) if pos['avg_cost'] > 0 else 0

            messages.append(
                f"卖出 {self._label(code)}\n"
                f"  {shares}股 ≈{net_cash:.0f}元\n"
                f"  {pos['avg_cost']:.3f}→{actual_price:.3f}\n"
                f"  盈亏{pnl:+.0f}元 {pnl_pct:+.1%}\n"
                f"  {target.reason}"
            )
            del self.positions[code]

        available_cash = free_cash + released_cash
        total_pool = available_cash

        if buy_targets:
            total_weight = sum(t.target_weight for t in buy_targets)
            for target in buy_targets:
                code = target.code
                price = self._get_price(code, data)
                if price <= 0:
                    continue

                weight_ratio = target.target_weight / total_weight if total_weight > 0 else 1 / len(buy_targets)
                money_available = total_pool * weight_ratio

                current_pos = self.positions.get(code, {})
                current_shares = current_pos.get('shares', 0)
                current_value = current_shares * price

                if current_shares > 0:
                    target_value = money_available + current_value
                    target_shares = int(target_value / (price * (1 + self.slippage)) / 100) * 100
                    shares = max(target_shares - current_shares, 0)
                else:
                    shares = int(money_available / (price * (1 + self.slippage)) / 100) * 100

                if shares <= 0:
                    continue

                actual_price = price * (1 + self.slippage)
                trade_value = shares * actual_price
                commission = max(trade_value * self.commission_rate, 0.0)
                total_cost = trade_value + commission

                if total_cost > available_cash:
                    shares = int((available_cash - self.min_commission) / (actual_price * (1 + self.commission_rate)) / 100) * 100
                    if shares <= 0:
                        continue
                    trade_value = shares * actual_price
                    commission = max(trade_value * self.commission_rate, 0.0)
                    total_cost = trade_value + commission

                if current_shares > 0:
                    new_avg = (current_pos['shares'] * current_pos['avg_cost'] + shares * actual_price) / (current_shares + shares)
                    new_hwm = max(current_pos.get('high_watermark', actual_price), actual_price)
                else:
                    new_avg = actual_price
                    new_hwm = actual_price

                messages.append(
                    f"买入 {self._label(code)}\n"
                    f"  {shares}股 ≈{total_cost:.0f}元\n"
                    f"  价{actual_price:.3f}\n"
                    f"  {target.reason}"
                )

                self.positions[code] = {
                    'shares': current_shares + shares,
                    'avg_cost': new_avg,
                    'high_watermark': new_hwm,
                }
                available_cash -= total_cost

        self.positions['free_capital'] = available_cash
        self.positions['last_update'] = today

        if self.notifier:
            prefix = f"【{self.name}】\n" if self.name else ""
            if messages:
                content = prefix + "\n\n".join(messages)
            else:
                content = f"{prefix}今日无操作"

            summary = self._build_portfolio_summary(data)
            content += "\n\n" + summary
            self.notifier.notify("交易操作", content)

        # 更新 prev_close 为当日收盘价，供次日计算当日盈亏
        self._update_prev_close(data)
        if not self.dry_run:
            self._save_positions()

        return {'messages': messages}

    def get_positions(self) -> dict[str, Any]:
        return {k: v for k, v in self.positions.items() if k not in ('free_capital', 'last_update')}

    def available_capital(self) -> float:
        return self.positions.get('free_capital', 0.0)

    def _holding_codes(self) -> list[str]:
        return [k for k in self.positions if k not in ('free_capital', 'last_update')]

    def _label(self, code: str) -> str:
        name = self.code_names.get(code)
        return f"{code} {name}" if name else code

    def _get_price(self, code: str, data: DataResponse) -> float:
        if code in data.bar_data and not data.bar_data[code].empty:
            return float(data.bar_data[code].iloc[-1]['close'])
        return 0.0

    def _load_positions(self) -> dict:
        if os.path.exists(self.position_file):
            with open(self.position_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {'free_capital': self.initial_capital, 'last_update': ''}

    def _save_positions(self):
        os.makedirs(os.path.dirname(self.position_file), exist_ok=True)
        with open(self.position_file, 'w', encoding='utf-8') as f:
            json.dump(self.positions, f, ensure_ascii=False, indent=2)

    def _build_portfolio_summary(self, data: DataResponse) -> str:
        """构建持仓盈亏报表：各标的当前市值、总盈亏、当日盈亏、现金余额、总资产。"""
        today = datetime.now().strftime('%Y-%m-%d')
        lines = [f"{today} 持仓:", "━━━━━━━━━━━━━━━━"]

        holding_codes = self._holding_codes()
        total_holding_value = 0.0
        total_pnl = 0.0
        total_daily_pnl = 0.0

        for code in sorted(holding_codes):
            pos = self.positions[code]
            shares = pos['shares']
            avg_cost = pos['avg_cost']
            price = self._get_price(code, data)
            if price <= 0:
                continue

            value = shares * price
            cost = shares * avg_cost
            pnl = value - cost
            pnl_pct = pnl / cost if cost > 0 else 0.0
            total_holding_value += value
            total_pnl += pnl

            # 当日盈亏：基于昨日收盘价
            prev_close = pos.get('prev_close', 0)
            if prev_close > 0:
                daily_pnl = shares * (price - prev_close)
                daily_pnl_pct = (price / prev_close - 1) * 100
            else:
                daily_pnl = 0.0
                daily_pnl_pct = 0.0
            total_daily_pnl += daily_pnl

            label = self._label(code)
            pnl_label = "浮盈" if pnl >= 0 else "浮亏"
            lines.append(label)
            lines.append(f"  市值{value:.0f}元")
            lines.append(f"  {pnl_label} {pnl:+.0f} {pnl_pct:+.1%}")
            if prev_close > 0:
                daily_label = "日盈" if daily_pnl >= 0 else "日亏"
                lines.append(f"  {daily_label} {daily_pnl:+.0f} {daily_pnl_pct:+.1%}")

        lines.append("━━━━━━━━━━━━━━━━")
        cash = self.positions.get('free_capital', 0.0)
        lines.append(f"现金 {cash:.0f}元")

        total_assets = total_holding_value + cash
        lines.append(f"合计 {total_assets:.0f}元")
        lines.append(f"总盈亏 {total_pnl:+.0f}")
        lines.append(f"当日盈亏 {total_daily_pnl:+.0f}")

        return "\n".join(lines)

    def _update_prev_close(self, data: DataResponse):
        """更新所有持仓的 prev_close 为当日收盘价，供次日计算当日盈亏"""
        for code in self._holding_codes():
            price = self._get_price(code, data)
            if price > 0:
                self.positions[code]['prev_close'] = price
