import os
import subprocess
import sys
import time

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_BASE_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from git import Repo
from loguru import logger

logger.remove()
logger.add(sys.stdout, level='INFO',
           format='<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}')

from quantforge.core.notifier import WeChatNotifier

notifier = WeChatNotifier()


def pull_from_remote(repo_path: str, branch_name: str) -> tuple[bool, str]:
    """强制同步远端代码。丢弃所有本地修改，以远端为准。"""
    try:
        repo = Repo(repo_path)

        if repo.head.is_detached:
            logger.info("HEAD 处于游离状态，先切换到目标分支")
            repo.git.checkout(branch_name)

        prev_commit = repo.head.commit
        logger.info(f"当前 HEAD: {prev_commit.hexsha[:8]}")

        # === 1. fetch 远端 ===
        logger.info(f"git fetch origin {branch_name} ...")
        result = subprocess.run(
            ["git", "fetch", "origin", branch_name],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            err = (result.stderr + result.stdout).strip()
            logger.error(f"git fetch 返回码={result.returncode}: {err}")
            if "could not read" in err.lower() or "permission denied" in err.lower():
                return False, f"认证失败: {err}"
            if "does not appear to be a git repository" in err:
                return False, f"仓库状态异常: {err}"
            return False, f"fetch 失败(rc={result.returncode}): {err}"

        remote_ref = f"origin/{branch_name}"

        # === 2. 检查是否有新提交 ===
        try:
            remote_commit = repo.commit(remote_ref)
        except Exception:
            logger.warning(f"远端引用 {remote_ref} 不存在，尝试首次拉取")
            repo.git.checkout("-b", branch_name, remote_ref)
            remote_commit = repo.head.commit

        if prev_commit.hexsha == remote_commit.hexsha:
            logger.info(f"分支 {branch_name} 无新提交")
            return True, f"分支 {branch_name} 无新提交"

        # === 3. 强制重置到远端（丢弃所有本地修改） ===
        logger.info(f"git reset --hard {remote_ref}（丢弃本地修改，以远端为准）")
        repo.git.reset("--hard", remote_ref)

        log_output = repo.git.log(
            f"{prev_commit.hexsha}..{remote_commit.hexsha}",
            "--pretty=format:%h - %s (%an, %ad)",
            "--date=short"
        )
        commits_list = log_output.strip().splitlines()
        commit_details = "\n".join([f"  ⦁ {c}" for c in commits_list])
        logger.info(f"强制同步成功: {len(commits_list)} 个新提交")
        return True, f"同步 {branch_name}，新增 {len(commits_list)} 个提交\n{commit_details}"

    except subprocess.TimeoutExpired:
        logger.error("git fetch 超时 (120s)")
        return False, "同步超时 (120s)"
    except Exception as e:
        logger.error(f"同步异常: {e}")
        return False, f"同步异常: {e.__class__.__name__}: {e}"


if __name__ == "__main__":
    report_text = ""
    for i in range(10):
        logger.info(f"第 {i+1}/10 次拉取...")
        result, git_info = pull_from_remote(os.getcwd(), "main")
        report_text = git_info
        if result:
            logger.info("拉取完成")
            break
        logger.warning(f"拉取失败，30秒后重试: {git_info}")
        time.sleep(30)
    else:
        logger.error("10次重试全部失败")
    notifier.notify("代码同步", report_text)
