# @layer: integration
"""T008 回测结果版本化管理测试"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from loguru import logger

from quantforge.research.versioning import get_git_info, save_git_info, update_run_index

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
logger.remove()
logger.add(sys.stderr, level="INFO",
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")


def test_get_git_info():
    """验证 Git 信息获取正常。"""
    logger.info("=== 测试 get_git_info ===")
    info = get_git_info(BASE_DIR)

    assert 'sha' in info, "缺少 sha"
    assert 'branch' in info, "缺少 branch"
    assert 'dirty' in info, "缺少 dirty"
    assert isinstance(info['dirty'], bool), "dirty 应为 bool"

    logger.info(f"  SHA: {info['sha']}, Branch: {info['branch']}, Dirty: {info['dirty']}")
    logger.success("get_git_info OK")


def test_save_git_info():
    """验证 Git 信息能正确保存。"""
    logger.info("=== 测试 save_git_info ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        run_dir = os.path.join(tmpdir, "run_test")
        os.makedirs(run_dir)

        info = save_git_info(run_dir, BASE_DIR)
        assert os.path.exists(os.path.join(run_dir, 'git_info.json')), "git_info.json 未创建"

        with open(os.path.join(run_dir, 'git_info.json'), 'r', encoding='utf-8') as f:
            saved = json.load(f)

        assert saved['sha'] == info['sha'], "sha 不匹配"
        assert saved.get('timestamp'), "缺少 timestamp"
        logger.success("save_git_info OK")


def test_update_run_index():
    """验证运行索引追加工作正常。"""
    logger.info("=== 测试 update_run_index ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        results_dir = os.path.join(tmpdir, "results")
        os.makedirs(results_dir)

        # 第一次追加
        update_run_index(
            os.path.join(results_dir, "run_1"),
            {'run_id': 'run_1', 'sha': 'abc', 'strategy': 'test',
             'total_return': 0.5, 'sharpe_ratio': 1.5},
            results_dir,
        )
        # 第二次追加
        update_run_index(
            os.path.join(results_dir, "run_2"),
            {'run_id': 'run_2', 'sha': 'def', 'strategy': 'test',
             'total_return': 0.6, 'sharpe_ratio': 1.8},
            results_dir,
        )

        index_path = os.path.join(results_dir, 'index.json')
        assert os.path.exists(index_path), "index.json 未创建"

        with open(index_path, 'r', encoding='utf-8') as f:
            entries = json.load(f)

        assert len(entries) == 2, f"应有 2 条记录，实际 {len(entries)}"
        assert entries[0]['run_id'] == 'run_1', "第一条记录不符"
        assert entries[1]['run_id'] == 'run_2', "第二条记录不符"

        logger.success(f"update_run_index OK: {len(entries)} 条记录")


def test_unknown_git():
    """验证非 Git 环境下优雅降级。"""
    logger.info("=== 测试非 Git 环境降级 ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        info = get_git_info(tmpdir)  # 非 git 目录
        assert info['sha'] == 'unknown', f"应降级为 unknown，实际: {info['sha']}"
        logger.success("非 Git 环境降级 OK")


def test_compare_runs():
    """验证对比工具在两个模拟 run 上正常工作。"""
    logger.info("=== 测试对比工具 ===")
    from quantforge.tools.compare_results import compare_runs, format_comparison

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建两个模拟 run
        run_a = os.path.join(tmpdir, "run_a")
        run_b = os.path.join(tmpdir, "run_b")
        os.makedirs(run_a)
        os.makedirs(run_b)

        report_a = {'total_return': 0.45, 'sharpe_ratio': 1.60, 'max_drawdown': 0.18,
                     'annual_return': 0.12, 'trade_count': 40, 'win_rate': 0.55,
                     'profit_factor': 1.2, 'excess_return': 0.05, 'sortino_ratio': 1.8}
        report_b = {'total_return': 0.52, 'sharpe_ratio': 1.75, 'max_drawdown': 0.15,
                     'annual_return': 0.14, 'trade_count': 38, 'win_rate': 0.58,
                     'profit_factor': 1.4, 'excess_return': 0.08, 'sortino_ratio': 2.0}

        with open(os.path.join(run_a, 'report.json'), 'w') as f:
            json.dump(report_a, f)
        with open(os.path.join(run_b, 'report.json'), 'w') as f:
            json.dump(report_b, f)

        with open(os.path.join(run_a, 'git_info.json'), 'w') as f:
            json.dump({'sha': 'abc123', 'branch': 'main'}, f)
        with open(os.path.join(run_b, 'git_info.json'), 'w') as f:
            json.dump({'sha': 'def456', 'branch': 'feat'}, f)

        result = compare_runs(run_a, run_b)
        assert 'error' not in result, f"对比失败: {result.get('error')}"

        diffs = result['diffs']
        assert diffs['总收益率']['diff'] > 0, "run_b 总收益应更高"
        assert float(diffs['Sharpe比率']['diff']) > 0, "run_b Sharpe 应更高"

        formatted = format_comparison(result)
        assert 'abc123' in formatted, "应包含 SHA"
        logger.info(formatted)
        logger.success("对比工具 OK")


if __name__ == "__main__":
    test_get_git_info()
    test_save_git_info()
    test_update_run_index()
    test_unknown_git()
    test_compare_runs()
    logger.success("\nT008 回测结果版本化管理测试完成")
