#!/usr/bin/env python3
"""生成交付汇报用的机械证据：基线、改动统计、删除符号、停用名字残留、fallback 特征。"""
import argparse
import codecs
import datetime
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import tempfile
import uuid

SCRIPT = os.path.abspath(__file__)
RUN_ID_RE = re.compile(r"[0-9a-f]{32}\Z")

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

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def die(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)


def git(args, cwd, ok_codes=(0,), env=None):
    p = subprocess.run(["git", "-c", "core.quotepath=off"] + args, cwd=cwd, env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode not in ok_codes:
        die("git %s 失败：%s" % (" ".join(args), p.stderr.decode("utf-8", "replace").strip()))
    return p.returncode, p.stdout.decode("utf-8", "surrogateescape")


def repo_root():
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        die('非 git 项目：无法生成机械证据，交付第 3 项写"无机械证据（非 git）"')
    return p.stdout.decode().strip()


def state_dir(root):
    _, gd = git(["rev-parse", "--absolute-git-dir"], root)
    return os.path.join(gd.rstrip("\n"), "change-report")


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as error:
        die(f"无法读取任务记录 {path}：{error}")


def write_json(path, data):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as f:
        try:
            json.dump(data, f, indent=2)
            f.close()
            os.replace(f.name, path)
        finally:
            if os.path.exists(f.name):
                os.unlink(f.name)


def run_dir(state, run):
    if not RUN_ID_RE.fullmatch(run):
        die(f"无效的任务编号：{run}")
    return os.path.join(state, "runs", run)


def load_baseline(state, run=None, optional=False):
    if run is None:
        path = os.path.join(state, "baseline.json")
        if not os.path.exists(path):
            if optional:
                return None
            die("未找到任务基线。请在编辑前运行 start，或用 report --base 指定比较基线。")
        latest = read_json(path)
        run = latest.get("run")
        if run is None:
            return latest
    directory = run_dir(state, run)
    path = os.path.join(directory, "baseline.json")
    if not os.path.exists(path):
        die(f"未找到任务 {run} 的基线。请核对任务编号和当前工作树。")
    baseline = read_json(path)
    names = list(baseline.get("old_names", []))
    for entry in sorted(os.listdir(directory)):
        if entry.startswith("old-") and entry.endswith(".json"):
            names.extend(read_json(os.path.join(directory, entry)))
    baseline["old_names"] = dedup(names)
    return baseline


def untracked(root):
    _, out = git(["ls-files", "-z", "--others", "--exclude-standard"], root)
    return [name for name in out.split("\0") if name]


def file_fingerprint(root, name):
    path = os.path.join(root, name)
    try:
        mode = os.lstat(path).st_mode
        if stat.S_ISLNK(mode):
            return "link:" + hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
        if stat.S_ISDIR(mode):
            return "directory"
        if not stat.S_ISREG(mode):
            return "unreadable"
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
        return f"file:{bool(mode & stat.S_IXUSR)}:{digest.hexdigest()}"
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unreadable"


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
    run = uuid.uuid4().hex
    state = state_dir(root)
    names = dedup(args.old or [])
    fingerprints = {name: file_fingerprint(root, name) for name in untracked(root)}
    git(["update-ref", f"refs/change-report/runs/{run}", base], root)
    write_json(os.path.join(run_dir(state, run), "baseline.json"), {
        "run": run, "base": base, "head": head, "untracked": fingerprints,
        "old_names": names, "created": now(),
    })
    write_json(os.path.join(state, "baseline.json"), {"run": run})
    print("基线已记录：%s（停用名字：%s）" % (base[:7], ", ".join(names) if names else "无"))
    print(f"任务编号：{run}")


def cmd_mark_old(args):
    state = state_dir(repo_root())
    baseline = load_baseline(state, args.run)
    names = dedup(args.names)
    path = os.path.join(run_dir(state, args.run), f"old-{uuid.uuid4().hex}.json")
    write_json(path, names)
    print(f"已登记停用名字：{', '.join(names)}（任务 {args.run}，基线 {baseline['base'][:7]}）")


def diff_filename(header):
    value = header[4:].rstrip("\t")
    if value == "/dev/null":
        return None
    if value.startswith('"') and value.endswith('"'):
        value = os.fsdecode(codecs.escape_decode(os.fsencode(value[1:-1]))[0])
    return value[2:]


def parse_diff(text):
    """提取删除行和新增行，保留文件名与各自的行号。"""
    removed, added = [], []
    old_file = new_file = None
    old_no = new_no = old_left = new_left = 0
    for line in text.split("\n"):
        if line.startswith("diff --git "):
            old_file = new_file = None
            old_left = new_left = 0
        elif line.startswith("@@ "):
            m = HUNK_RE.match(line)
            if m:
                old_no, new_no = int(m.group(1)), int(m.group(3))
                old_left, new_left = int(m.group(2) or 1), int(m.group(4) or 1)
        elif old_left or new_left:
            if line.startswith("-") and old_file is not None:
                removed.append((old_file, old_no, line[1:]))
                old_no += 1
                old_left -= 1
            elif line.startswith("+") and new_file is not None:
                added.append((new_file, new_no, line[1:]))
                new_no += 1
                new_left -= 1
            elif line.startswith(" "):
                old_no += 1
                new_no += 1
                old_left -= 1
                new_left -= 1
        elif line.startswith("--- "):
            old_file = diff_filename(line)
        elif line.startswith("+++ "):
            new_file = diff_filename(line)
    return removed, added


def symbols(content):
    return {m.group(1) for p in DEF_PATTERNS for m in [p.search(content)] if m}


def cmd_report(args):
    root = repo_root()
    state = state_dir(root)
    if args.base:
        code, sha = git(["rev-parse", "--verify", "-q", "--end-of-options", args.base + "^{commit}"], root, ok_codes=(0, 1))
        if code != 0:
            die("无效的基线：%s" % args.base)
        base = sha.strip()
        latest = load_baseline(state, optional=True)
        baseline = latest if latest and latest.get("base") == base else None
    else:
        baseline = load_baseline(state, args.run)
        base = baseline["base"]

    if baseline is None:
        print("比较范围：指定 Git 基线。缺少任务元数据，无法比较未跟踪文件或读取停用名登记。")
    elif args.run:
        print(f"比较范围：任务 {args.run} 的基线与当前工作树。")
    else:
        print(f"比较范围：使用最近记录的匹配基线（任务 {baseline.get('run', '旧版基线')}）。")
    print("Git 差异汇总工作树最终内容，不单列暂存状态。基线未记录的 Git 忽略文件不纳入比较。")
    names = dedup((baseline or {}).get("old_names", []) + (args.old or []))
    base_untracked = (baseline or {}).get("untracked", {})
    if isinstance(base_untracked, list):
        base_untracked = dict.fromkeys(base_untracked)

    untracked_stat, untracked_added, untracked_unknown = [], [], []
    for name, before in base_untracked.items():
        current = file_fingerprint(root, name)
        if before in (None, "missing", "unreadable", "directory") or current == "unreadable":
            untracked_unknown.append(f" {name}（缺少可比较的摘要）")
        elif current == "missing":
            untracked_stat.append(f" {name}（原未跟踪文件已删除）")
        elif current != before:
            untracked_stat.append(f" {name}（原未跟踪文件有变化）")

    tracked_paths = ["--", "."]
    if base_untracked:
        # 只过滤已暂存的同名路径。文件变成目录后，其下的新文件仍纳入比较。
        _, tracked = git(["ls-files", "-z"], root)
        indexed = set(tracked.split("\0"))
        tracked_paths.extend(f":(exclude,literal){name}" for name in base_untracked if name in indexed)
    current_untracked = untracked(root)
    for name in current_untracked if baseline is not None else []:
        if name in base_untracked:
            continue
        path = os.path.join(root, name)
        try:
            mode = os.lstat(path).st_mode
            if not stat.S_ISREG(mode):
                untracked_stat.append(f" {name}（新未跟踪路径，未扫描内容）")
                continue
            with open(path, "rb") as f:
                raw = f.read()
            if b"\0" in raw:
                untracked_stat.append(f" {name}（新未跟踪文件，二进制内容未扫描）")
                continue
            lines = raw.decode("utf-8", "surrogateescape").splitlines()
            untracked_stat.append(f" {name} | {len(lines)} (新文件，未跟踪)")
            untracked_added.extend((name, i, line) for i, line in enumerate(lines, 1))
        except OSError:
            untracked_unknown.append(f" {name}（当前无法读取）")

    print("== 改动统计（基线 %s）==" % base[:7])
    diff_flags = ["diff", "--no-renames", "--no-ext-diff", "--no-textconv"]
    _, stats = git(diff_flags + ["--stat", base] + tracked_paths, root)
    stat_lines = stats.rstrip("\n").splitlines() + untracked_stat + untracked_unknown
    if baseline is None or untracked_unknown:
        print("\n".join(stat_lines) if stat_lines else "未检出 Git 差异；未跟踪文件未完成比较。")
    else:
        print("\n".join(stat_lines) if stat_lines else "在本次比较范围内未检出改动")
    if base_untracked:
        print("基线时已存在的未跟踪文件仅比较摘要，无法核对其逐行增删和 fallback 特征。")
        print("符号链接只比较目标路径，不读取目标内容。")
    if baseline is None and current_untracked:
        print(f"当前有 {len(current_untracked)} 个未跟踪路径，未判断是否属于本次改动。")

    _, diff = git(diff_flags + ["-U0", "--no-color", "--src-prefix=a/", "--dst-prefix=b/", base] + tracked_paths, root)
    removed, added = parse_diff(diff)
    added.extend(untracked_added)
    _, deleted = git(diff_flags + ["--name-only", "-z", "--diff-filter=D", base] + tracked_paths, root)

    print("\n== 删除的符号与文件（符号为启发式候选） ==")
    added_syms = {(file, name) for file, _, content in added for name in symbols(content)}
    out = []
    for f, n, c in removed:
        for s in sorted(symbols(c)):
            note = "（同文件新增行有同名定义，需核对）" if (f, s) in added_syms else ""
            out.append(f"{f}:{n} {s}{note}")
    out += ["%s（文件从 Git 比较中删除）" % f for f in deleted.split("\0") if f]
    print("\n".join(out) if out else "未检出")

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

    run = (baseline or {}).get("run")
    ref_args = f"--run {run}" if run else f"--base {base}"
    old_args = " --old " + " ".join(shlex.quote(name) for name in args.old) if args.old else ""
    print(f"\n证据生成于 {now()}；对当前工作树复跑：python3 {shlex.quote(SCRIPT)} report {ref_args}{old_args}")


def main():
    ap = argparse.ArgumentParser(description="交付汇报机械证据")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="为新任务记录基线")
    s.add_argument("--old", nargs="+", metavar="NAME", help="登记停用名字")
    m = sub.add_parser("mark-old", help="为已有任务追加停用名字，沿用原基线")
    m.add_argument("--run", required=True, metavar="ID", help="start 输出的任务编号")
    m.add_argument("names", nargs="+", metavar="NAME")
    r = sub.add_parser("report", help="输出证据")
    r.add_argument("--old", nargs="+", metavar="NAME", help="追加停用名字")
    basis = r.add_mutually_exclusive_group()
    basis.add_argument("--run", metavar="ID", help="使用指定任务的基线")
    basis.add_argument("--base", metavar="REV", help="指定 Git 比较基线")
    args = ap.parse_args()
    {"start": cmd_start, "mark-old": cmd_mark_old, "report": cmd_report}[args.cmd](args)


if __name__ == "__main__":
    main()
