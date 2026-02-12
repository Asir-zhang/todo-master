---
name: todo-master
description: 用于本地待办管理的技能，支持长期和短期任务、按时间范围查询、按月分文件归档；首次必须初始化存储路径（默认或自定义绝对路径），未初始化时禁止执行业务命令。
---

# Todo Skill（OpenClaw）

## 技能目的

这个技能提供本地待办管理能力，包含三类能力：

1. 写能力：新增长期/短期待办
2. 读能力：按时间范围查询待办
3. 管理能力：按月分文件存储，而不是单一大文件

## 运行结构

脚本入口：

```bash
python3 skills/todo/scripts/todo.py
```

配置文件：

1. `skills/todo/config.json`

数据文件（初始化后自动创建）：

1. `skills/todo/data/index.json`
2. `skills/todo/data/todos-YYYY-MM.json`

初始化是强制步骤：

1. 未初始化时，除 `init` 外所有命令都被禁止执行
2. 首次必须二选一：自定义绝对路径 或 默认路径

## 数据规则

1. `type=long`：`due_date` 可选
2. `type=short`：`plan_date` 必填
3. `rm` 为软删除（`status=deleted`）
4. 默认时区为中国时区（`Asia/Shanghai`）
5. 长期待办归档月份优先使用 `due_date`

## 命令说明

初始化（第一步，必做）：

```bash
python3 skills/todo/scripts/todo.py init --default
python3 skills/todo/scripts/todo.py init --data-dir /absolute/path
```

新增：

```bash
python3 skills/todo/scripts/todo.py add --type long --title "准备Q2方案" --due 2026-03-20 --tag work
python3 skills/todo/scripts/todo.py add --type short --title "提交报销材料" --plan 2026-02-14
```

查询：

```bash
python3 skills/todo/scripts/todo.py list
python3 skills/todo/scripts/todo.py list --status all --by due --from 2026-02-01 --to 2026-02-28
python3 skills/todo/scripts/todo.py list --status open --due-state overdue
python3 skills/todo/scripts/todo.py list --status open --due-state not-overdue
python3 skills/todo/scripts/todo.py show --id <todo_id>
python3 skills/todo/scripts/todo.py overdue
```

管理：

```bash
python3 skills/todo/scripts/todo.py done --id <todo_id>
python3 skills/todo/scripts/todo.py reopen --id <todo_id>
python3 skills/todo/scripts/todo.py cancel --id <todo_id>
python3 skills/todo/scripts/todo.py rm --id <todo_id>
python3 skills/todo/scripts/todo.py update --id <todo_id> --title "新标题" --due 2026-03-25
```

## 默认行为

1. `list` 默认筛选 `status=open`
2. `list` 默认时间口径为 `by=created`
3. `list` 默认逾期筛选为 `due-state=all`
4. 默认输出为文本，传 `--json` 输出 JSON
5. 若 `config.json` 的 `data_dir` 为空，默认路径为 `skills/todo/data`

## 错误约定

非零退出会输出单行错误前缀：

1. `ERR_VALIDATION`
2. `ERR_NOT_FOUND`
3. `ERR_STORAGE`
4. `ERR_CORRUPTION`
5. `ERR_NOT_INITIALIZED`

## 给代理的执行规则

1. 所有增删改查必须走 CLI，不要手工改 JSON
2. 每次写操作后回显 `id` 和关键字段
3. 用户未给截止日期时不要臆测
4. 若未初始化，先引导执行 `init`
