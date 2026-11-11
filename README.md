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

1. **动手前**：为本次任务记录 Git 基线。请求含"替换/改成/迁移"时，先回一行
   `替换确认 → 停用：<具体名字> | 新增：<具体名字>`，写不出停用侧的名字就停下来问。
2. **交付时**：运行脚本生成机械证据（改动统计、被删符号、停用名字残留、fallback 特征），原样贴出，再按必报类别写汇报。简单改动用两行精简版。
3. **落盘**：在项目根 `.ai-changes.md` 顶部插入一条，写给以后的人看。

你也可以直接在聊天里说“改前画一下这条路径”。AI 会用 `[A]`、`[B]` 等编号标出当前逻辑和拟改位置。回复“展开 B”查看步骤，或回复“按图改，把 B 放到 C”指定实现位置。改后沿用编号，从源码检查是否按意图实现。报告同时保留 Git 证据和实际测试结果。

## 安装

```sh
cp -r change-report ~/.codex/skills/          # Codex
cp -r change-report ~/.agents/skills/         # 其他支持 Agent Skills 的环境
```

## 用法

```sh
python3 scripts/change_report.py start
python3 scripts/change_report.py mark-old --run "$run_id" fetchUser
python3 scripts/change_report.py report --run "$run_id"
```

把 `start` 输出的任务编号填入 `run_id`。首次记录时，也可以用 `start --old fetchUser` 一并登记。后续用 `mark-old` 追加，基线保持原样。

`report --run` 使用指定任务的基线，适合多个任务交错进行。裸 `report` 使用当前工作树最近记录的基线。`report --base` 可指定 Git 引用，若没有匹配的最近任务元数据，报告会注明未跟踪文件和停用名登记的覆盖范围。

输出示例：

```
== 改动统计（基线 5b8ce34）==
 src/user.ts | 16 +++++++++++-----
 1 file changed, 11 insertions(+), 5 deletions(-)

== 删除的符号与文件（符号为启发式候选） ==
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

- 报告比较基线和当前工作树，暂存状态不单列。用报告中的 `--run` 命令可复跑，结果随工作树变化。
- 改前已有的未跟踪文件只保存内容摘要，可发现变化和删除。逐行差异无法从摘要恢复。Git 忽略且未被记录的文件不在比较范围内。
- 停用名字 grep 为 0 不代表删干净：动态引用、字符串拼接、配置、仓库外的调用方都查不到。
- fallback 是关键词扫描，有误报也有漏报；命中不等于错，只是必须逐条说明。
- 删除行上的定义是候选。其他文件出现同名新增时，原位置的候选仍会显示。同文件同名新增会提示核对。

## 开发

```sh
python3 -m unittest discover -s tests -v
```

脚本只用 Python 标准库和 Git。任务元数据存在当前工作树的 Git 目录下，快照由 `refs/change-report/runs/` 下的引用保留。
