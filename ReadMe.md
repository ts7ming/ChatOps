# ChatOps

基于钉钉 Stream 的运维机器人，集成 **DolphinScheduler**、**SeaTunnel** 与 **SQLBot**，通过钉钉消息即可查询任务状态、触发工作流、操作 SeaTunnel job、执行 SQL。

> 仅授权用户可在钉钉中向机器人发送指令，机器人会通过对应模块调用下游系统并以可读文本回复。

---

## 功能模块

| 前缀    | 模块                        | 用途                                                                 |
|---------|-----------------------------|----------------------------------------------------------------------|
| `ds`    | `dolphinscheduler/`         | DolphinScheduler 工作流查询 / 触发 / 紧急强重置                       |
| `st`    | `seatunnel/`                | SeaTunnel 作业查询、暂停、保存、恢复、取消                            |
| `sqlbot`| `sqlbot/`                   | 在 `settings.DATABASES` 已配置的库上执行 SQL（查询/执行）              |

---

## 项目结构

```
ChatOps/
├── main.py                          # 启动入口，注册钉钉 stream 客户端和 handler_map
├── settings_example.py              # 配置模板（复制为 settings.py 后使用）
├── test_bot.py
├── dingtalk_allowed_users.txt       # 钉钉机器人白名单（senderId 每行一个，热加载）
├── dingtalk/
│   └── dingtalk_helper.py           # 消息入口：解析首词路由到具体模块，按 mtime 读白名单
├── dolphinscheduler/
│   ├── dolphin_base.py              # DS REST API 封装
│   └── dolphin_helper.py            # ds 查询/执行 业务逻辑
├── seatunnel/
│   ├── seatunnel_base.py            # SeaTunnel REST API 封装
│   └── seatunnel_helper.py          # st 查询/暂停/保存/恢复/取消
├── sqlbot/
│   └── sqlbot_helper.py             # 基于 pyqueen DataSource 的 SQL 执行
└── tools/
    └── print_user_id.py             # 监听机器人消息，打印 senderId 收集白名单
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install dingtalk-stream pyqueen aiohttp
```

### 2. 准备配置

```bash
cp settings_example.py settings.py
```

按需修改 `settings.py`：

- `DING_APP_ID` / `DING_APP_SECRET` — 钉钉机器人凭证
- `DINGTALK_ALLOWED_USERS` — 允许使用机器人的 `senderId` 列表，留空 `[]` 表示关闭鉴权（不推荐生产使用）
- `DS_BASE_URL` / `DS_TOKEN` — DolphinScheduler
- `SEATUNNEL_BASE_URL` / `SEATUNNEL_CLUSTER_NAME` / `SEATUNNEL_JOB_ID_MAP` — SeaTunnel
- `DATABASES` — 供 `sqlbot` 与 `ds.scheduler_hard_reset` 使用的数据库配置（`conn_type` 支持 `mysql` / `pgsql` / `clickhouse` / `redis` 等 pyqueen 支持的类型）
- `HARD_RESET_EXCLUDE_TASKS` / `HARD_RESET_EXCLUDE_WORKFLOW_PATTERNS` — 强重置时的排除名单

### 3. 启动

```bash
python main.py
```

机器人启动后保持运行，钉钉里向机器人发送对应前缀的指令即可。

---

## 钉钉指令

> 钉钉消息按空格切分。模块首词决定走哪个 handler；模块内首词为动作。

### `ds` — DolphinScheduler

```
ds 查询                                # 项目下最近 5 条工作流实例
ds 查询 <项目>                         # 同上
ds 查询 <项目> <工作流>                # 该工作流最近实例的任务
ds 查询 <项目> <工作流> <任务>         # 在最近若干实例中定位该任务
ds 执行 <项目> <工作流>                # 触发工作流
```

### `st` — SeaTunnel

```
st 查询                                # 运行中任务（最多 5 个，不足时用最近已完成补齐）
st 暂停 <jobId或名称>                  # 保存 savepoint 后停止
st 保存 <jobId或名称>                  # 触发 savepoint / checkpoint
st 恢复 <jobId或名称>                  # 从 savepoint 恢复
st 取消 <jobId或名称>                  # 直接停止，不保留 savepoint
```

> `jobId或名称` 可以是 `SEATUNNEL_JOB_ID_MAP` 中配置的友好名，例：`{"etl_order_sync": "1111111111111111111"}`。

### `sqlbot` — 数据库执行

```
sqlbot 查询 <db_name> <sql...>          # 走 ds.read_sql(sql)，DataFrame → Markdown 表格
sqlbot 执行 <db_name> <sql...>          # 走 ds.exe_sql(sql, auto_commit=True)
```

`<db_name>` 必须是 `settings.DATABASES` 中已定义的 key，例如 `BI`、`etl-config`。查询结果最多展示 100 行。

示例：

```
sqlbot 查询 BI select id, name from users limit 10
sqlbot 执行 etl-config update t set flag=1 where id=123
```

---

## 鉴权

`dingtalk/dingtalk_helper.py` 会检查消息发送者的 `senderId`（即 staffId）。

白名单改为 **项目根目录下的 `dingtalk_allowed_users.txt` 文件**，每行一个 `senderId`：

```
# 钉钉机器人白名单
# 每行一个 senderId (= staffId)，以 # 开头的行视为注释
STAFF1234567890  # 张三
STAFF9876543210  # 李四
```

- 文件不存在或为空 → 拒绝所有（安全默认）
- 修改保存后机器人按文件 mtime 自动重载，**无需重启服务**
- 自定义路径：在 `settings.py` 中设置 `DINGTALK_ALLOWED_USERS_FILE = "/path/to/whitelist.txt"`

获取 `senderId` 的方法见下一节[《收集用户 ID》](#收集用户-id)。

---

## 注意事项

- **`DolphinHelper.scheduler_hard_reset()`** 是危险操作，会绕过调度器状态机直写 MySQL，仅用于调度器卡死时的紧急恢复，请通过 `HARD_RESET_EXCLUDE_*` 排除关键任务/工作流。
- **`sqlbot 执行`** 会真正写入数据库，请妥善控制 `DINGTALK_ALLOWED_USERS`。
- 同步阻塞的 pyqueen 调用（`sqlbot`、`scheduler_hard_reset`）通过 `asyncio.to_thread` 包装，不会卡住事件循环。
- SeaTunnel 模块使用 RESTful API v2：`/running-jobs`、`/finished-jobs/{state}`、`/job-info/{jobId}` 等；如部署的 SeaTunnel 版本路径不同，需要调整 `seatunnel_base.py`。

---

## 开发提示

- 新增模块：在项目根新建目录 + `__init__.py` + 业务类，实现 `async def process(self, cmd)`；在 `main.py` 的 `handler_map` 中注册首词即可被钉钉路由到。
- `cmd` 已经是去除首词后的列表，直接按索引取参即可。
- 异常最好在 `process` 内捕获并返回可读文本，否则会被 `dingtalk_helper` 捕获为 `处理失败: <str(e)>`。

## 收集用户 ID

白名单文件 `dingtalk_allowed_users.txt` 位于项目根，每行一个 `senderId`（即 `staffId`）。两种最简单的方式，按使用场景选：

### 方式一：从启动日志里抓（无需额外工具）

1. 启动机器人：`python main.py`
2. 让目标用户在钉钉里向机器人发送任意消息（如 `hi`）
3. 在控制台日志里找到形如下面那一行，括号里就是 `senderId`：

   ```
   2026-10-12 10:30:15 WARNING 用户: 张三(STAFF1234567890) ---- 未授权 [dingtalk_helper.py:93]
   ```

4. 把 id 追加到 `dingtalk_allowed_users.txt`，保存即生效（无需重启）：

   ```
   # 钉钉机器人白名单
   STAFF1234567890  # 张三
   ```

### 方式二：专用收集工具（多用户批量收集更方便）

```bash
python tools/print_user_id.py
```

启动后控制台会实时输出：

```
============================================================
监听中, 请让目标用户在钉钉里向机器人发送任意消息 (Ctrl+C 退出)
白名单文件: d:/Project/ChatOps/dingtalk_allowed_users.txt
============================================================
------------------------------------------------------------
[新] 昵称: 张三
     id  : STAFF1234567890
     已追加 → d:/Project/ChatOps/dingtalk_allowed_users.txt
------------------------------------------------------------
[已知] 李四(STAFF9876543210)  消息: hi
```

- 新用户首次出现会**自动追加到 `dingtalk_allowed_users.txt`**，自动去重
- 让所有目标用户依次给机器人发消息，最后 `dingtalk_allowed_users.txt` 形如：

  ```
  # 钉钉机器人白名单
  # 每行一个 senderId (= staffId)，以 # 开头的行视为注释
  STAFF1234567890  # 张三
  STAFF9876543210  # 李四
  ```

- 工具写入后，机器人下次消息自动按 mtime 重载，**无需重启**

> 小贴士：`senderId` 在钉钉里就是用户的 `staffId`，同一员工不会变；`senderNick` 只是昵称显示用，鉴权只看 `senderId`。
