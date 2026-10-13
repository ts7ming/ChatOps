import asyncio
import os

from pyqueen import DataSource
from settings import DATABASES
from dolphinscheduler.dolphin_base import DolphinBase

# ds 重置使用的数据库 key（settings.DATABASES 中需存在）
_HARD_RESET_DB = 'DS'

# ds 重置 SQL 文件 (txt)，固定项目根目录，按 mtime 热加载
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS_EXCLUDE_FILE = os.path.join(_PROJECT_ROOT, "ds_hard_reset_exclude.txt")

# 查询时默认展示的实例/任务条数
_DEFAULT_DISPLAY = 5


class DolphinHelper(DolphinBase):
    def __init__(self, base_url, ds_token=None, token=None):
        super().__init__(base_url=base_url, token=token if token is not None else ds_token)

    @staticmethod
    def _parse_sql_file(path):
        """
        读取 SQL 文件, 返回 (statements, file_mtime)。

        文件格式:
            - 每行一条 SQL, 用 ';' 分号作为结束符 (允许跨行)
            - 整行注释: 以 '#' 开头的行整行跳过
            - 段头标记: '[...]' 一行视为注释跳过
            - 文件不存在 → 返回 ([], -1.0)
        """
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return [], -1.0

        statements = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                buf = []
                for line in f:
                    raw = line.strip()
                    if not raw or raw.startswith("#") or raw.startswith("["):
                        continue
                    # 去掉行内 # 注释 (无引号时)
                    if "#" in raw and "'" not in raw and '"' not in raw:
                        raw = raw.split("#", 1)[0].strip()
                    if not raw:
                        continue
                    buf.append(raw)
                    if raw.endswith(";"):
                        stmt = " ".join(buf).rstrip(";").strip()
                        if stmt:
                            statements.append(stmt)
                        buf = []
                # 末尾无分号也保留
                if buf:
                    stmt = " ".join(buf).strip()
                    if stmt:
                        statements.append(stmt)
        except OSError:
            pass
        return statements, mtime

    def scheduler_hard_reset(self):
        """
        危险操作: 读取 ds_hard_reset_exclude.txt 中的 SQL 逐条执行。

        适用场景: 调度器内部状态机卡死, STOP/KILL API 全部失效时的紧急恢复。

        文件格式: 逐行写完整 SQL, 用 ';' 分号结束, '#' 开头的行为注释。
        例:
            UPDATE t_ds_task_instance SET state = 6 WHERE state NOT IN (6, 7);

        :return: [(sql_snippet, rowcount), ...] 每条 SQL 及其受影响行数
        """
        if _HARD_RESET_DB not in DATABASES:
            raise KeyError(
                f"settings.DATABASES 中未配置 '{_HARD_RESET_DB}', "
                f"请在 settings.py 添加该 key 或设置 HARD_RESET_DB = '已存在的key'"
            )

        path = DS_EXCLUDE_FILE
        statements, _ = self._parse_sql_file(path)
        if not statements:
            return []

        ds = DataSource(**DATABASES[_HARD_RESET_DB])
        results = []
        try:
            for stmt in statements:
                rowcount = ds.exe_sql(stmt, auto_commit=True)
                snippet = (stmt[:60] + "...") if len(stmt) > 60 else stmt
                results.append((snippet, rowcount))
        finally:
            try:
                ds.close_conn()
            except Exception:
                pass
        return results

    async def _hard_reset(self):
        """
        异步包装的 ds 重置操作, 避免阻塞事件循环。
        SQL 通过项目根目录的 ds_hard_reset_exclude.txt 文件配置 (热加载)。
        """
        try:
            results = await asyncio.to_thread(self.scheduler_hard_reset)
        except KeyError as e:
            return f"配置错误: {e}"
        except Exception as e:
            return f"重置失败: {e}"

        if not results:
            return (
                f"ds 重置文件 {os.path.basename(DS_EXCLUDE_FILE)} 中没有可执行的 SQL。\n"
                f"文件路径: {DS_EXCLUDE_FILE}"
            )

        lines = [f"ds 重置完成 (共执行 {len(results)} 条 SQL):"]
        for snippet, rc in results:
            lines.append(f"- {snippet} -> {rc} 行受影响")
        lines.append(f"\n执行源文件: {DS_EXCLUDE_FILE}")
        return "\n".join(lines)

    async def process(self, cmd):
        """
        解析并执行一条 ds 命令。

        :param cmd: 列表, 元素依次是:
            cmd[0] - 动作: '查询' / '执行'
            cmd[1] - 项目名 (必填)
            cmd[2] - 工作流名 (执行时必填, 查询时可选)
            cmd[3] - 任务名 (查询时可选)
        :return: 给钉钉用户看的字符串; 失败时也是可读错误说明

        命令矩阵:
            [查询]                          → 项目下最近 5 条工作流实例
            [查询, 项目, 工作流]            → 该工作流最近 5 条实例的任务
            [查询, 项目, 工作流, 任务]      → 在最近 5 个实例里定位该任务
            [执行, 项目, 工作流]            → 触发工作流
            [重置]                          → 紧急强重置
        """
        try:
            if not cmd:
                return self._help()
            action = cmd[0]

            # ---------- 重置  ----------
            if action in ('重置', '重置调度', 'hard_reset', 'scheduler_hard_reset'):
                if len(cmd) > 1:
                    return "格式错误。重置命令不需要额外参数。\nSQL 模板请在项目根目录的 ds_hard_reset_exclude.txt 文件中配置。"
                return await self._hard_reset()

            # ---------- 查询 ----------
            if action == '查询':
                if len(cmd) < 2:
                    return "格式错误。请使用: 查询 [项目名] [工作流名(可选)] [任务名(可选)]"

                project_name = cmd[1]
                workflow_name = cmd[2] if len(cmd) > 2 else None
                task_name = cmd[3] if len(cmd) > 3 else None

                project_code = await self.find_project_code(project_name)
                if not project_code:
                    return f"未找到名为 '{project_name}' 的项目。"

                # 优先级: 指定任务 > 指定工作流 > 仅有项目
                if task_name:
                    return await self._format_task(
                        project_name, project_code, workflow_name, task_name
                    )
                if workflow_name:
                    return await self._format_workflow_tasks(
                        project_name, project_code, workflow_name
                    )
                return await self._format_project_instances(project_name, project_code)

            # ---------- 执行 ----------
            if action == '执行':
                if len(cmd) < 3:
                    return "格式错误。请使用: 执行 [项目名] [工作流名]"
                return await self._do_run(cmd[1], cmd[2])

            return self._help()
        except Exception as e:
            return f"处理失败: {str(e)}"

    # ========== 查询分支的格式化方法 ==========

    async def _format_task(self, project_name, project_code, workflow_name, task_name):
        """三参数模式: 在最近若干个实例里定位指定任务。"""
        instance, task = await self.find_task_in_instances(
            project_code, workflow_name, task_name
        )
        if not instance:
            return (
                f"在项目 '{project_name}' 的工作流 '{workflow_name}' "
                f"中未找到任务 '{task_name}' 的实例记录。"
            )
        return (
            f"找到任务:\n"
            f"- 任务名: {task.get('name')}\n"
            f"- 状态: {task.get('state')}\n"
            f"- 所属实例: {instance.get('name')} (ID: {instance.get('id')})\n"
            f"- 主机: {task.get('host', 'N/A')}\n"
            f"- 开始时间: {task.get('startTime', 'N/A')}"
        )

    async def _format_workflow_tasks(self, project_name, project_code, workflow_name):
        """两参数模式: 列出该工作流最近 N 条实例, 并把每个实例的任务列出来。"""
        instances = await self.list_workflow_instances(project_code, workflow_name)
        if instances.get("code") != 0:
            return f"查询失败: {instances.get('msg')}"

        total_list = instances.get("data", {}).get("totalList", [])
        if not total_list:
            return f"项目 '{project_name}' 下未找到工作流 '{workflow_name}' 的实例。"

        lines = [
            f"项目 [{project_name}] 工作流 [{workflow_name}] "
            f"最近 {min(_DEFAULT_DISPLAY, len(total_list))} 条实例的任务："
        ]
        for inst in total_list[:_DEFAULT_DISPLAY]:
            instance_id = inst.get("id")
            tasks_result = await self.get_instance_tasks(project_code, instance_id)
            if tasks_result.get("code") != 0:
                continue
            # DS 偶发返回 data 为空列表, get('taskList') 会抛错, 这里用 or {} 兜底
            task_list = (tasks_result.get("data") or {}).get('taskList') or []
            if not task_list:
                continue
            lines.append(f"\n实例 {inst.get('name')} (ID: {instance_id}):")
            for task in task_list[:_DEFAULT_DISPLAY]:
                lines.append(
                    f"- {task.get('name')} | 状态: {task.get('state')} "
                    f"| 开始: {task.get('startTime', 'N/A')}"
                )
        return "\n".join(lines)

    async def _format_project_instances(self, project_name, project_code):
        """单参数模式: 列出项目下最近 N 条工作流实例。"""
        instances = await self.list_workflow_instances(project_code, None)
        if instances.get("code") != 0:
            return f"查询失败: {instances.get('msg')}"

        total_list = instances.get("data", {}).get("totalList", [])
        if not total_list:
            return f"项目 '{project_name}' 下没有找到工作流实例。"

        lines = [f"项目 [{project_name}] 最近 {len(total_list)} 条工作流实例："]
        for inst in total_list[:_DEFAULT_DISPLAY]:
            name = inst.get("name", "未知")
            state = inst.get("state", "未知")
            start_time = inst.get("startTime", "")
            instance_id = inst.get("id", "")
            lines.append(
                f"- {name} | 状态: {state} | 开始: {start_time} | ID: {instance_id}"
            )
        return "\n".join(lines)

    # ========== 执行分支 ==========

    async def _do_run(self, project_name, workflow_name):
        """
        触发工作流执行。

        DS 的工作流名 ≠ workflowDefinitionCode, 需要先查定义列表拿到 code 再触发。
        """
        project_code = await self.find_project_code(project_name)
        if not project_code:
            return f"未找到名为 '{project_name}' 的项目。"

        definitions = await self.list_workflow_definition(project_code, workflow_name)
        total_list = definitions.get("data", {}).get("totalList", [])
        if not total_list:
            return f"项目 '{project_name}' 下未找到名为 '{workflow_name}' 的工作流定义。"

        wf_code = total_list[0].get("code")
        result = await self.start_workflow_instance(project_code, wf_code)
        if result.get("code") == 0:
            return f"工作流 '{workflow_name}' 已触发执行。"
        return f"执行失败: {result.get('msg')}"

    # ========== 帮助 ==========

    @staticmethod
    def _help():
        return (
            "支持的指令：\n"
            "1. 查询 [项目名] [工作流名(可选)] [任务名(可选)]\n"
            "2. 执行 [项目名] [工作流名]\n"
            "3. 重置    (绕过调度器把所有非 SUCCESS/FAILURE 状态强标为 FAILURE)\n"
        )
