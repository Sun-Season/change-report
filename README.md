# change-report

让 AI 在交付代码改动时，把**真正做了什么**讲全：删了什么、额外改了什么、替你做了哪些决定、哪些没验证。

不是防止 AI 撒谎，是让它没法用一句"已修复"糊过去。

## 解决什么

用 AI 写代码时常见的四种情况：

- 说要"替换"，实际只新增，旧实现还在，还加了你没要的 fallback；
- 说"改过了、测过了"，你不用逐行看代码就无法判断真假；
- 它自己定了默认值、错误处理、兼容分支，因为觉得不重要而没说；
- 几天后有人问起这次改了什么，只能再问 AI，还可能问不出来。

## 做法

三件事，都在 AI 编码会话里完成：

1. **动手前**：记录 git 基线。请求含"替换/改成/迁移"时，先回一行
   `替换确认 → 停用：<具体名字> | 新增：<具体名字>`，写不出停用侧的名字就停下来问。
2. **交付时**：运行脚本生成机械证据（改动统计、被删符号、停用名字残留、fallback 特征），原样贴出，再按必报类别写汇报。简单改动用两行精简版。
3. **落盘**：在项目根 `.ai-changes.md` 顶部插入一条，写给以后的人看。

原则只有一句：**事实来自命令输出，AI 只写理由和决定。**

## 安装

```sh
cp -r change-report ~/.codex/skills/          # Codex
cp -r change-report ~/.agents/skills/         # 其他支持 Agent Skills 的环境
```

## 用法

```sh
python3 scripts/change_report.py start                    # 动手前
python3 scripts/change_report.py start --old fetchUser    # 登记要停用的名字
python3 scripts/change_report.py report                   # 交付时
python3 scripts/change_report.py report --base <sha>      # 复查
```

输出示例：

```
== 改动统计（基线 5b8ce34）==
 src/user.ts | 16 +++++++++++-----
 1 file changed, 11 insertions(+), 5 deletions(-)

== 删除的符号与文件 ==
src/user.ts:5 loadDetail

== 停用名字残留 ==
注意：0 处不代表删干净（动态引用/字符串拼接/配置/外部调用方查不到）
fetchUser: 2 处
  src/service.ts:2: export function fetchUser(...)
  src/service.ts:8: const u = fetchUser(id);

== 新增代码中的 fallback 特征 ==
命中 1 处
src/user.ts:10: } catch (e) {
```

## 边界（别当它比你聪明）

- 只能缩小撒谎空间，不能消除：贴出的输出仍可能被改写，用 `--base` 复跑一次即可核对。
- 停用名字 grep 为 0 不代表删干净：动态引用、字符串拼接、配置、仓库外的调用方都查不到。
- fallback 是关键词扫描，有误报也有漏报；命中不等于错，只是必须逐条说明。
- 符号识别是启发式的，大小写敏感；全大写定义（如 SQL 的 `CREATE FUNCTION`）识别不到。

## 开发

```sh
python3 -m py_compile scripts/change_report.py
```

脚本只用标准库，依赖 `git`。基线存在 `.git/change-report/baseline.json` 和 `refs/change-report/base`，不污染工作区。
