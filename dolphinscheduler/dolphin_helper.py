from pyqueen import DataSource
from settings import (
    DATABASES,
    HARD_RESET_EXCLUDE_TASKS,
    HARD_RESET_EXCLUDE_WORKFLOW_PATTERNS,
)
from dolphinscheduler.dolphin_base import DolphinBase, TASK_SEARCH_INSTANCE_LIMIT


# DS 任务/工作流状态码 (state 字段) 节选:
#   0 SUBMITTED, 1 RUNNING, 2 READY_PAUSE, 3 PAUSE, 4 READY_STOP,
#   5 STOP, 6 FAILURE, 7 SUCCESS, 8 NEED_FAULT_TOLERANCE, 9 KILL
# scheduler_hard_reset 把"非 6/7"的状态直接 UPDATE 成 6, 绕过 STOP/KILL 流程。
_DS_STATE_FAILURE = '6'
_DS_STATE_SUCCESS = '7'


_DEFAULT_DISPLAY = 5


class DolphinHelper(DolphinBase):
    def __init__(self, base_url, ds_token=None, token=None):
        super().__init__(base_url=base_url, token=token if token is not None else ds_token)

    def scheduler_hard_reset(self):
        """
        危险操作: 绕过调度器, 直写 MySQL 强重置所有运行中的任务和工作流。

        适用场景: 调度器内部状态机卡死, STOP/KILL API 全部失效时的紧急恢复。
        实现原理: 把"非 FAILURE/非 SUCCESS"的状态直接 UPDATE 为 FAILURE (state=6)。

        排除名单由 settings.py 控制:
          - HARD_RESET_EXCLUDE_TASKS           : 任务名不在此列表内才被重置
          - HARD_RESET_EXCLUDE_WORKFLOW_PATTERNS: 工作流名 NOT LIKE 任一模式才被重置
        """
        ds = DataSource(**DATABASES['etl-config'])

        # 任务表: name NOT IN (...)
        task_exclude = ""
        if HARD_RESET_EXCLUDE_TASKS:
            names = ", ".join(f"'{t}'" for t in HARD_RESET_EXCLUDE_TASKS)
            task_exclude = f" AND `name` NOT IN ({names})"

        # 工作流表: 多条 NOT LIKE 用 AND 连接
        wf_exclude = ""
        if HARD_RESET_EXCLUDE_WORKFLOW_PATTERNS:
            clauses = " AND ".join(
                f"`name` NOT LIKE '{p}'" for p in HARD_RESET_EXCLUDE_WORKFLOW_PATTERNS
            )
            wf_exclude = f" AND {clauses}"

        sql_task = (
            f"update dolphinscheduler.t_ds_task_instance set `state` = {_DS_STATE_FAILURE} "
            f"WHERE `state` <> '{_DS_STATE_SUCCESS}' AND `state` <> '{_DS_STATE_FAILURE}'"
            f"{task_exclude}"
        )
        sql_wf = (
            f"update `dolphinscheduler`.`t_ds_workflow_instance` set state = {_DS_STATE_FAILURE} "
            f"WHERE `state` <> '{_DS_STATE_SUCCESS}' AND `state` <> '{_DS_STATE_FAILURE}'"
            f"{wf_exclude}"
        )
        ds.exe_sql(sql_task)
        ds.exe_sql(sql_wf)

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
        """
        try:
            if not cmd:
                return self._help()
            action = cmd[0]

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
        )
