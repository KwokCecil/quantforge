# @layer: unit
"""测试统一运行入口。glob 自动发现所有 _test_*.py 和 _verify_*.py。
通过文件首行的 # @layer: 标记自动区分快慢测试，无需维护硬编码列表。

用法:
    python run_all_tests.py                 # 默认：只跑 unit + contract（<5s）
    python run_all_tests.py --all           # 跑所有（含 integration/e2e，需网络）
    python run_all_tests.py --layers unit   # 只跑 unit 层

新增测试文件只需:
    1. 命名为 _test_xxx.py 或 _verify_xxx.py
    2. 文件第一行写 # @layer: unit | contract | integration | e2e
    → 自动被纳入，无需改本脚本
"""
import subprocess
import sys
import time
import os
from pathlib import Path

TEST_DIR = Path(__file__).parent
PROJECT_PARENT = str(TEST_DIR.parent.parent)  # quantforge/ 的父目录，使 from quantforge.xxx 可导入

FAST_LAYERS = {"unit", "contract"}


def _layer_of(filepath: Path) -> str:
    """从文件前5行提取 @layer 标记"""
    with open(filepath, encoding="utf-8") as f:
        for _ in range(5):
            line = f.readline()
            if not line:
                break
            if line.startswith("# @layer:"):
                return line.split(":", 1)[1].strip()
    return "unknown"


def discover_tests() -> list[tuple[Path, str]]:
    """glob 发现所有测试文件，返回 (文件路径, layer) 列表"""
    files = sorted(TEST_DIR.glob("_test_*.py")) + sorted(TEST_DIR.glob("_verify_*.py"))
    return [(f, _layer_of(f)) for f in files]


def run_test(filepath: Path) -> tuple[bool, float, str]:
    """返回 (通过, 耗时秒, 最后3行stderr)"""
    start = time.perf_counter()
    env = os.environ.copy()
    existing_pythonpath = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = f"{PROJECT_PARENT}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else PROJECT_PARENT
    result = subprocess.run(
        [sys.executable, str(filepath)],
        capture_output=True, text=True, timeout=120,
        cwd=str(TEST_DIR.parent),
        env=env,
    )
    elapsed = time.perf_counter() - start
    ok = result.returncode == 0
    stderr_tail = ""
    if not ok:
        lines = (result.stderr + result.stdout).strip().splitlines()
        stderr_tail = "\n".join(lines[-3:]) if lines else "(no output)"
    return ok, elapsed, stderr_tail


def main():
    import argparse
    parser = argparse.ArgumentParser(description="QuantForge 测试运行器")
    parser.add_argument("--all", action="store_true", help="运行所有层测试（含慢速网络测试）")
    parser.add_argument("--layers", type=str, default="",
                        help="指定层: unit,contract,integration,e2e（逗号分隔）")
    args = parser.parse_args()

    tests = discover_tests()
    if not tests:
        print("未找到测试文件")
        sys.exit(0)

    if args.layers:
        target_layers = set(s.strip() for s in args.layers.split(","))
    elif args.all:
        target_layers = None
    else:
        target_layers = FAST_LAYERS

    # 按层分组
    layer_order = ["unit", "contract", "integration", "e2e", "unknown"]
    by_layer: dict[str, list[Path]] = {lo: [] for lo in layer_order}
    for fp, layer in tests:
        by_layer.setdefault(layer, []).append(fp)

    passed = 0
    failed = 0
    skipped = 0
    total_time = 0.0

    for layer in layer_order:
        files = by_layer.get(layer, [])
        if not files:
            continue

        if target_layers is not None and layer not in target_layers:
            print(f"\n{'='*50}")
            print(f"  {layer.upper()} (skipped)")
            print(f"{'='*50}")
            for fp in files:
                print(f"  [SKIP] {fp.name}")
            skipped += len(files)
            continue

        print(f"\n{'='*50}")
        print(f"  {layer.upper()}")
        print(f"{'='*50}")

        for fp in files:
            print(f"  RUN  {fp.name} ...", end="", flush=True)
            ok, elapsed, tail = run_test(fp)
            total_time += elapsed
            if ok:
                passed += 1
                print(f"  PASS ({elapsed:.1f}s)")
            else:
                failed += 1
                print(f"  FAIL ({elapsed:.1f}s)")
                print(f"     {tail}")

    print(f"\n{'='*50}")
    total = passed + failed + skipped
    print(f"  {passed} passed  {failed} failed  {skipped} skipped  ({total_time:.1f}s total)")
    print(f"{'='*50}")

    if not args.all and not args.layers:
        print("\n提示: 默认只跑 unit + contract，用 --all 跑全部（含网络测试）")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
