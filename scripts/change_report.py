#!/usr/bin/env python3
"""生成交付汇报用的机械证据：基线、改动统计、删除符号、停用名字残留、fallback 特征。"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

SCRIPT = os.path.abspath(__file__)
BASE_REF = "refs/change-report/base"

DEF_PATTERNS = [re.compile(p) for p in (
    r"^\s*(?:async\s+)?def\s+(\w+)",
    r"^\s*class\s+(\w+)",
    r"function\s*\*?\s+(\w+)",
    r"^\s*(?:export\s+)?(?:default\s+)?(?:const|let|var)\s+(\w+)\s*=",
    r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)",
    r"^\s*(?:export\s+)?(?:interface|type|enum)\s+(\w+)",
    r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)",
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:fn|struct|enum|trait)\s+(\w+)",
)]

FALLBACK_RE = re.compile("|".join((
    r"\bcatch\b", r"\bexcept\b", r"fallback", r"legacy", r"compat", r"兼容", r"降级",
    r"\bretry\b", r"\bretries\b", r"\bdeprecated\b",
    r"except[^:]*:\s*pass", r"catch\s*(\([^)]*\))?\s*\{\s*\}",
)), re.IGNORECASE)

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def git(args, cwd, ok_codes=(0,), env=None):
    p = subprocess.run(["git", "-c", "core.quotepath=off"] + args, cwd=cwd, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode not in ok_codes:
        die("git %s 失败：%s" % (" ".join(args), p.stderr.decode("utf-8", "replace").strip()))
    return p.returncode, p.stdout.decode("utf-8", "replace")


def repo_root():
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        die('非 git 项目：无法生成机械证据，交付第 3 项写"无机械证据（非 git）"')
    return p.stdout.decode().strip()


def baseline_path(root):
    _, gd = git(["rev-parse", "--absolute-git-dir"], root)
    return os.path.join(gd.strip(), "change-report", "baseline.json")


def untracked(root):
    _, out = git(["ls-files", "--others", "--exclude-standard"], root)
    return [l for l in out.splitlines() if l]


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def dedup(items):
    seen, res = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            res.append(i)
    return res


def cmd_start(args):
    root = repo_root()
    code, head = git(["rev-parse", "--verify", "-q", "HEAD"], root, ok_codes=(0, 1))
    if code != 0:
        die("仓库还没有任何提交，无法记录基线")
    head = head.strip()
    env = dict(os.environ, GIT_AUTHOR_NAME="change-report", GIT_AUTHOR_EMAIL="change-report@local",
               GIT_COMMITTER_NAME="change-report", GIT_COMMITTER_EMAIL="change-report@local")
    _, stash = git(["stash", "create"], root, env=env)
    base = stash.strip() or head
    git(["update-ref", BASE_REF, base], root)
    path = baseline_path(root)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                print("已覆盖旧基线 %s" % json.load(f).get("base", "")[:7], file=sys.stderr)
        except (OSError, ValueError):
            print("已覆盖旧基线（旧文件无法解析）", file=sys.stderr)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    names = dedup(args.old or [])
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"base": base, "head": head, "untracked": untracked(root),
                   "old_names": names, "created": now()}, f, ensure_ascii=False, indent=2)
    print("基线已记录：%s（停用名字：%s）" % (base[:7], ", ".join(names) if names else "无"))


def parse_diff(text):
    """返回 (removed, added, deleted_files)；removed/added 为 [(file, lineno, content)]。"""
    removed, added, deleted = [], [], []
    old_file = new_file = None
    old_no = new_no = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            old_file = new_file = None
        # 只认真正的文件头（a/ b/ 前缀或 /dev/null），否则 diff 正文里以 "-- " 开头的行
        # （SQL、Lua、Haskell 注释）会变成 "--- ..."，被误当成文件头，导致后面的删除行被跳过。
        elif line.startswith("--- ") and (line[4:] == "/dev/null" or line[4:].startswith("a/")):
            old_file = None if line[4:] == "/dev/null" else line[4:][2:]
        elif line.startswith("+++ ") and (line[4:] == "/dev/null" or line[4:].startswith("b/")):
            new_file = None if line[4:] == "/dev/null" else line[4:][2:]
            if new_file is None and old_file:
                deleted.append(old_file)
        elif line.startswith("@@"):
            m = HUNK_RE.match(line)
            if m:
                old_no, new_no = int(m.group(1)), int(m.group(2))
        elif line.startswith("-") and old_file is not None:
            removed.append((old_file, old_no, line[1:]))
            old_no += 1
        elif line.startswith("+") and new_file is not None:
            added.append((new_file, new_no, line[1:]))
            new_no += 1
    return removed, added, deleted


def symbols(content):
    return {m.group(1) for p in DEF_PATTERNS for m in [p.search(content)] if m}


def cmd_report(args):
    root = repo_root()
    if args.base:
        code, sha = git(["rev-parse", "--verify", "-q", args.base + "^{commit}"], root, ok_codes=(0, 1))
        if code != 0:
            die("无效的基线：%s" % args.base)
        base, base_untracked, baseline_old = sha.strip(), [], []
        # --base 复跑时也读回 baseline.json，否则停用名字与基线未跟踪文件会丢，
        # 复跑结果和首次报告不一致（SKILL 里给的复跑命令正是这种用法）。
        path = baseline_path(root)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    b = json.load(f)
                if b.get("base") == base:
                    base_untracked = b.get("untracked", [])
                    baseline_old = b.get("old_names", [])
            except (OSError, ValueError):
                pass
        names = dedup(baseline_old + (args.old or []))
    else:
        path = baseline_path(root)
        if not os.path.exists(path):
            die("未找到基线，请先运行 start，或用 --base <rev> 指定")
        with open(path, encoding="utf-8") as f:
            b = json.load(f)
        base, base_untracked = b["base"], b.get("untracked", [])
        names = dedup(b.get("old_names", []) + (args.old or []))

    known = set(base_untracked)
    new_untracked = [u for u in untracked(root) if u not in known]
    untracked_lines = []
    for u in new_untracked:
        try:
            with open(os.path.join(root, u), "rb") as f:
                raw = f.read()
            if b"\0" in raw:
                continue
            untracked_lines.append((u, raw.decode("utf-8", "replace").splitlines()))
        except OSError:
            continue

    print("== 改动统计（基线 %s）==" % base[:7])
    _, stat = git(["diff", "--stat", "--no-renames", base], root)
    stat_lines = stat.rstrip("\n").splitlines()
    summary = stat_lines.pop() if stat_lines else None
    stat_lines += [" %s | %d (新文件，未跟踪)" % (u, len(lines)) for u, lines in untracked_lines]
    if summary:
        stat_lines.append(summary)
    print("\n".join(stat_lines) if stat_lines else "无改动")

    _, diff = git(["diff", "-U0", "--no-color", "--no-ext-diff", "--no-renames",
                   "--src-prefix=a/", "--dst-prefix=b/", base], root)
    removed, added, deleted = parse_diff(diff)
    for u, lines in untracked_lines:
        added.extend((u, i + 1, l) for i, l in enumerate(lines))

    print("\n== 删除的符号与文件 ==")
    added_syms = set()
    for _, _, c in added:
        added_syms |= symbols(c)
    out = []
    for f, n, c in removed:
        for s in sorted(symbols(c) - added_syms):
            out.append("%s:%d %s" % (f, n, s))
    out += ["%s（文件已删除）" % f for f in deleted]
    print("\n".join(out) if out else "无")

    print("\n== 停用名字残留 ==")
    print("注意：0 处不代表删干净（动态引用/字符串拼接/配置/外部调用方查不到）")
    if not names:
        print("未登记停用名字")
    for name in names:
        # 排除落盘记录本身：它按规范会写"删了 <名字>"，否则每次 report 都会自我误报。
        _, hits = git(["grep", "-n", "-w", "-F", "--untracked", "-e", name, "--",
                       ".", ":(exclude).ai-changes.md"], root, ok_codes=(0, 1))
        hits = [h for h in hits.splitlines() if h]
        print("%s: %d 处" % (name, len(hits)))
        for h in hits[:20]:
            print("  " + h)
        if len(hits) > 20:
            print("  ……另有 %d 处" % (len(hits) - 20))

    print("\n== 新增代码中的 fallback 特征 ==")
    fb = [(f, n, c.strip()) for f, n, c in added if FALLBACK_RE.search(c)]
    print("命中 %d 处" % len(fb))
    for f, n, c in fb:
        print("%s:%d: %s" % (f, n, c if len(c) <= 120 else c[:120] + "…"))

    print("\n证据生成于 %s；复跑：python3 %s report --base %s" % (now(), SCRIPT, base))


def main():
    ap = argparse.ArgumentParser(description="交付汇报机械证据")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="记录基线")
    s.add_argument("--old", nargs="+", metavar="NAME", help="登记停用名字")
    r = sub.add_parser("report", help="输出证据")
    r.add_argument("--old", nargs="+", metavar="NAME", help="追加停用名字")
    r.add_argument("--base", metavar="REV", help="指定基线，忽略 baseline.json")
    args = ap.parse_args()
    cmd_start(args) if args.cmd == "start" else cmd_report(args)


if __name__ == "__main__":
    main()
