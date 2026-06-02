"""上线前预检脚本。验证策略上线所需的全部前置条件，任何阻塞项失败则返回非0退出码。

用法:
    .venv/Scripts/python.exe main_preflight.py           # 基础检查（必须通过）
    .venv/Scripts/python.exe main_preflight.py --full    # 完整检查（含数据源+干跑）
    .venv/Scripts/python.exe main_preflight.py -v        # 详细输出

退出码: 0=PASS, 1=FAIL（有阻塞项未通过）
"""
import argparse
import json
import os
import sys

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 确保 quantforge 包可以被导入（兼容无 PYTHONPATH 环境）
_PARENT_DIR = os.path.dirname(_BASE_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

CHECK_RESULTS: list = []


def _record(check_name: str, passed: bool, msg: str,
            blocking: bool = True, detail: str = ""):
    """记录一项检查结果。"""
    CHECK_RESULTS.append({
        "name": check_name,
        "passed": passed,
        "msg": msg,
        "blocking": blocking,
        "detail": detail,
    })


def _print_result(verbose: bool = False):
    """打印当前所有检查结果。"""
    for r in CHECK_RESULTS:
        icon = "[OK]" if r["passed"] else "[FAIL]"
        tag = "[BLOCK]" if r["blocking"] else "[OPT]"
        line = f"  {icon} {tag} {r['name']}: {r['msg']}"
        print(line)
        if verbose and r["detail"]:
            print(f"      详情: {r['detail']}")


# ============================================================
# 1. Token 检查
# ============================================================
def check_tokens(verbose: bool = False):
    """检查 tokens/ 下秘钥文件是否存在，notifier 能否实例化。"""
    # 检查 token 文件是否存在
    token_files = [
        ("tokens/wechat_webhook.py", "WECHAT_WEBHOOK_KEY"),
        ("tokens/小熊同学token.py", "AUTOSTOCK_API_TOKEN"),
        ("tokens/email_config.py", "EMAIL_CONFIG"),
    ]
    all_exist = True
    for rel_path, label in token_files:
        full_path = os.path.join(_BASE_DIR, rel_path)
        if os.path.exists(full_path):
            _record(f"Token文件[{label}]", True, rel_path,
                    detail=(f"验证通过: {full_path}" if verbose else ""))
        else:
            all_exist = False
            _record(f"Token文件[{label}]", False,
                    f"文件缺失: {rel_path}，请从 _templates 复制并填入真实值",
                    detail=f"期望路径: {full_path}")

    if not all_exist:
        return

    # 验证 notifier 能否实例化
    try:
        from quantforge.core.notifier import WeChatNotifier
        wn = WeChatNotifier()
        if hasattr(wn, 'webhook_url') and wn.webhook_url:
            _record("WeChatNotifier实例化", True, "成功",
                    detail=(wn.webhook_url[:60] + "..." if verbose else ""))
        else:
            _record("WeChatNotifier实例化", False, "webhook_url为空",
                    detail="请检查 tokens/wechat_webhook.py 中 webhook_key 是否有效")
    except Exception as e:
        _record("WeChatNotifier实例化", False, f"失败: {e}",
                detail=str(e))

    try:
        from quantforge.core.notifier import EmailNotifier
        en = EmailNotifier()
        if hasattr(en, 'sender') and en.sender:
            _record("EmailNotifier实例化", True, "成功",
                    detail=(f"sender={en.sender}" if verbose else ""))
        else:
            _record("EmailNotifier实例化", False, "sender为空",
                    detail="请检查 tokens/email_config.py 中 sender 是否有效")
    except Exception as e:
        _record("EmailNotifier实例化", False, f"失败: {e}",
                detail=str(e))


# ============================================================
# 2. 配置检查
# ============================================================
def check_config(verbose: bool = False):
    """检查 roc_momentum/tech_growth.json 是否存在且格式合法。"""
    config_path = os.path.join(
        _BASE_DIR, "config", "strategies", "roc_momentum", "tech_growth.json"
    )

    if not os.path.exists(config_path):
        _record("策略配置文件", False, f"文件不存在: {config_path}")
        return

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        _record("策略配置文件", False, f"JSON解析失败: {e}")
        return

    _record("策略配置文件", True, f"存在且格式合法 (config/strategies/roc_momentum/tech_growth.json)",
            detail=(f"包含 {len(cfg)} 个字段" if verbose else ""))

    # 检查关键字段
    required_fields = ["start_date", "codes", "initial_capital", "top_k"]
    for field in required_fields:
        if field in cfg:
            _record(f"配置字段[{field}]", True, f"值={cfg[field]}",
                    detail=(str(cfg[field]) if verbose else ""))
        else:
            _record(f"配置字段[{field}]", False, f"缺失必填字段",
                    detail=f"config JSON 中缺少 '{field}'")

    # 验证 codes 不为空
    codes = cfg.get("codes", [])
    if not codes:
        _record("配置字段[codes]", False, "codes列表为空",
                detail="至少需要一个交易代码")
    else:
        _record(f"交易代码数量", True, f"{len(codes)} 个代码",
                detail=(str(codes[:5]) + ("..." if len(codes) > 5 else "") if verbose else ""))


# ============================================================
# 3. 工厂检查
# ============================================================
def check_factory(verbose: bool = False):
    """检查 create_strategy() 和 create_config() 能否正常返回。"""
    try:
        from quantforge.strategies.factory import create_config
        config = create_config("roc_momentum", "tech_growth")
        _record("create_config", True, "成功创建ROCConfig",
                detail=(f"codes={len(config.codes)}只, initial_capital={config.initial_capital}" if verbose else ""))
    except Exception as e:
        _record("create_config", False, f"失败: {e}",
                detail=str(e))
        return

    try:
        from quantforge.strategies.factory import create_strategy
        strategy = create_strategy("roc_momentum", "tech_growth")
        _record("create_strategy", True, f"成功创建 {type(strategy).__name__}",
                detail=(str(type(strategy)) if verbose else ""))
    except Exception as e:
        _record("create_strategy", False, f"失败: {e}",
                detail=str(e))


# ============================================================
# 4. 核心导入检查
# ============================================================
def check_core_imports(verbose: bool = False):
    """检查所有核心模块能否导入。"""
    modules = [
        ("quantforge.core.executor", "LiveExecutor"),
        ("quantforge.core.notifier", "WeChatNotifier, EmailNotifier"),
        ("quantforge.core.resolver", "RankingResolver, TimingResolver"),
        ("quantforge.core.data_feed", "CachedDataFeed, DataRequest"),
        ("quantforge.strategies.roc_momentum", "ROCStrategy"),
        ("quantforge.data_sources.autostock_feed", "AutoStockFeed"),
        ("quantforge.tools.time_utils", "is_stock_trading_day"),
    ]
    for mod_name, desc in modules:
        try:
            __import__(mod_name)
            _record(f"导入 [{mod_name}]", True, desc,
                    detail=("OK" if verbose else ""))
        except Exception as e:
            _record(f"导入 [{mod_name}]", False, f"失败: {e}",
                    detail=str(e))


# ============================================================
# 5. 数据源连通性检查（--full）
# ============================================================
def check_data_feed(verbose: bool = False):
    """验证 AutoStockFeed 能否拉取基础数据。"""
    try:
        from quantforge.strategies.factory import create_config
        config = create_config("roc_momentum", "tech_growth")
    except Exception as e:
        _record("数据源连通性(创建配置)", False, f"前置失败: {e}", blocking=False, detail=str(e))
        return

    try:
        from quantforge.core.data_feed import CachedDataFeed, DataRequest
        from quantforge.data_sources.autostock_feed import AutoStockFeed

        data_feed = CachedDataFeed(
            source=AutoStockFeed(),
            cache_dir=os.path.join(_BASE_DIR, 'data'),
        )

        # 尝试拉取少量代码的缓存数据
        test_codes = config.codes[:3] if config.codes else ["510300"]
        start = config.start_date or "2025-12-01"
        end = config.end_date or "2025-12-31"

        data_feed.update_cache(
            codes=test_codes,
            data_type=config.data_type,
            start=start,
            end=end,
        )

        response = data_feed.get_data(DataRequest(
            codes=test_codes,
            data_type=config.data_type,
            start=start,
            end=end,
        ))

        bar_count = sum(len(df) for df in response.bar_data.values()
                        if hasattr(df, '__len__'))
        _record("数据源连通性(拉取K线)", True,
                f"成功拉取 {len(response.bar_data)} 只代码数据", blocking=False,
                detail=(f"bar_count={bar_count}, codes={test_codes}" if verbose else ""))
    except Exception as e:
        _record("数据源连通性(拉取K线)", False, f"失败: {e}", blocking=False,
                detail=str(e))


# ============================================================
# 6. 持仓文件检查
# ============================================================
def check_position_file(verbose: bool = False):
    """检查 position.json 是否存在及结构完整性。"""
    position_file = os.path.join(_BASE_DIR, "position", "position.json")

    if not os.path.exists(position_file):
        _record("持仓文件", True, "文件不存在（首次运行正常，LiveExecutor会创建默认结构）",
                detail="首次使用需手动确认初始状态")
        return

    try:
        with open(position_file, "r", encoding="utf-8") as f:
            pos = json.load(f)
    except json.JSONDecodeError as e:
        _record("持仓文件", False, f"JSON解析失败: {e}",
                detail=f"请检查 {position_file} 格式")
        return

    if "free_capital" not in pos:
        _record("持仓文件结构", False, "缺少 free_capital 字段",
                detail="LiveExecutor 需要此字段管理资金")
        return
    if "last_update" not in pos:
        _record("持仓文件结构", False, "缺少 last_update 字段",
                detail="LiveExecutor 需要此字段判断持仓时效")
        return

    holding_count = len([k for k in pos if k not in ("free_capital", "last_update")])
    _record("持仓文件", True,
            f"结构完整 (free_capital={pos['free_capital']}, 持仓={holding_count}只, last_update={pos['last_update'][:10]})",
            detail=(str(pos) if verbose else ""))


# ============================================================
# 7. 干跑仿真（--full）
# ============================================================
def check_dry_run(verbose: bool = False):
    """以 noop-notifier 模式完整跑一遍决策管道。"""
    class _NoopNotifier:
        def notify(self, *args, **kwargs):
            pass

    try:
        from quantforge.strategies.factory import create_strategy, create_config
        config = create_config("roc_momentum", "tech_growth")
        strategy = create_strategy("roc_momentum", "tech_growth")
    except Exception as e:
        _record("干跑仿真(创建策略)", False, f"前置失败: {e}", blocking=False, detail=str(e))
        return

    try:
        from quantforge.core.data_feed import CachedDataFeed, DataRequest
        from quantforge.core.executor import LiveExecutor
        from quantforge.core.resolver import RankingResolver
        from quantforge.data_sources.autostock_feed import AutoStockFeed

        data_feed = CachedDataFeed(
            source=AutoStockFeed(),
            cache_dir=os.path.join(_BASE_DIR, 'data'),
        )

        if config.inverse_vol_weight:
            weight_method = 'inverse_vol'
        elif config.BUY_AVERAGE:
            weight_method = 'equal'
        else:
            weight_method = 'signal_weight'
        resolver = RankingResolver(
            top_k=config.top_k,
            weight_method=weight_method,
            high_watermark_stop_edge=config.high_watermark_stop_edge if config.HIGH_WATERMARK_STOP else float('inf'),
            cut_loss_edge=config.cut_loss_edge if config.CUT_LOSS else float('inf'),
            top_k_sell=config.TOP_K_SELL,
        )

        executor = LiveExecutor(
            notifier=_NoopNotifier(),
            position_file=os.path.join(_BASE_DIR, 'position', 'position.json'),
            initial_capital=config.initial_capital,
            code_names=config.code_names,
        )

        data_feed.update_cache(
            codes=config.codes,
            data_type=config.data_type,
            start=config.start_date,
            end=config.end_date,
        )

        response = data_feed.get_data(DataRequest(
            codes=config.codes,
            data_type=config.data_type,
            start=config.start_date,
            end=config.end_date,
        ))

        decisions = strategy.produce_decisions(response, executor.get_positions())
        targets = resolver.resolve(
            decisions, executor.get_positions(),
            executor.available_capital(), response,
        )
        # 不调用 execute 以避免修改 position.json

        buy_count = sum(1 for t in targets if t.target_weight > 0)
        sell_count = sum(1 for t in targets if t.target_weight == 0 and t.code in executor._holding_codes())

        _record("干跑仿真", True,
                f"管道正常: {len(decisions)} 决策 -> {len(targets)} 目标 (买{buy_count}/卖{sell_count})", blocking=False,
                detail=(f"decisions={[str(d) for d in decisions[:3]]}..." if verbose else ""))
    except Exception as e:
        _record("干跑仿真", False, f"管道执行失败: {e}", blocking=False,
                detail=str(e))


# ============================================================
# 主入口
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="QuantForge 上线前预检")
    parser.add_argument("--full", action="store_true",
                        help="执行完整检查（含数据源连通性+干跑仿真，较慢）")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="详细输出")
    parser.add_argument("--strategy", type=str, default="roc_momentum",
                        help="要检查的策略名（目前仅支持 roc_momentum）")
    args = parser.parse_args()

    if args.strategy != "roc_momentum":
        print(f"[FAIL] 暂不支持策略: {args.strategy}")
        return 1

    print("=" * 56)
    print("  QuantForge 上线前预检")
    print("=" * 56)

    # ---- 阻塞检查（必须通过） ----
    print("\n--- [阻塞] Token检查 ---")
    check_tokens(verbose=args.verbose)

    print("\n--- [阻塞] 配置检查 ---")
    check_config(verbose=args.verbose)

    print("\n--- [阻塞] 工厂检查 ---")
    check_factory(verbose=args.verbose)

    print("\n--- [阻塞] 核心导入检查 ---")
    check_core_imports(verbose=args.verbose)

    # ---- 可选检查（--full） ----
    if args.full:
        print("\n--- [可选] 数据源连通性 ---")
        check_data_feed(verbose=args.verbose)

        print("\n--- [可选] 持仓文件检查 ---")
        check_position_file(verbose=args.verbose)

        print("\n--- [可选] 干跑仿真 ---")
        check_dry_run(verbose=args.verbose)

    # ---- 汇总 ----
    print("\n" + "=" * 56)
    print("  检查结果汇总")
    print("=" * 56)

    _print_result(verbose=args.verbose)

    blocking_failures = [
        r for r in CHECK_RESULTS
        if not r["passed"] and r["blocking"]
    ]
    optional_failures = [
        r for r in CHECK_RESULTS
        if not r["passed"] and not r["blocking"]
    ]

    total = len(CHECK_RESULTS)
    passed = sum(1 for r in CHECK_RESULTS if r["passed"])
    failed = total - passed

    print(f"\n总计: {total} 项, 通过: {passed}, 失败: {failed}")
    if blocking_failures:
        print(f"\n[FAIL] {len(blocking_failures)} 个阻塞项未通过:")
        for r in blocking_failures:
            print(f"  - {r['name']}: {r['msg']}")
    if optional_failures:
        print(f"\n[WARN] {len(optional_failures)} 个可选项未通过:")
        for r in optional_failures:
            print(f"  - {r['name']}: {r['msg']}")

    if blocking_failures:
        print("\n结论: FAIL — 存在阻塞项未通过，不能启动监控。")
        return 1
    else:
        if optional_failures:
            print("\n结论: WEAK_PASS — 阻塞项全部通过，可选项有失败，可启动监控。")
        else:
            print("\n结论: PASS — 全部检查通过，可启动监控。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
