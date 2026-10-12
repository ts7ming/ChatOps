import json
import aiohttp


class DolphinBase:
    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip('/')
        self.headers = {"token": token, "Accept": "application/json"}

    async def _request(self, method, path, params=None, payload=None, form=False):
        """
        底层 GET/POST 通用实现。

        :param method: 'GET' 或 'POST'
        :param path: 相对路径, 例如 '/projects/list'
        :param params: URL query 参数
        :param payload: 请求体; form=True 时序列化为 form-urlencoded, 否则 JSON
        :param form: True 表示用 form-urlencoded (部分 DS 接口要求)
        :return: 解析后的 JSON dict
        :raises Exception: 返回 HTML 时 (典型场景: 路径错或 Token 无效跳到登录页)
        """
        url = f"{self.base_url}{path}"
        async with aiohttp.ClientSession() as session:
            kwargs = {"headers": self.headers, "params": params}
            if form:
                kwargs["data"] = payload or {}
            else:
                kwargs["json"] = payload or {}
            async with session.request(method, url, **kwargs) as resp:
                content_type = resp.headers.get('Content-Type', '')
                print(f"[DS] {method} {url} -> Status: {resp.status}, Content-Type: {content_type}")
                text = await resp.text()
                # DS 未鉴权或路径错时, 会被网关/反向代理重定向到登录页 (HTML)
                if text.strip().startswith('<'):
                    print(f"[DS] 服务器返回了 HTML, 内容开头:\n{text[:500]}")
                    raise Exception(
                        f"API 返回了 HTML 而非 JSON。状态码: {resp.status}, "
                        f"请检查路径 '{path}' 和 Token 是否正确。"
                    )
                result = json.loads(text)
                # 截断到 2000 字符, 避免大响应刷屏; 调试时足够定位问题
                print("[DS] 响应:", json.dumps(result, ensure_ascii=False, indent=2)[:2000])
                return result

    async def _get(self, path, params=None):
        return await self._request("GET", path, params=params)

    async def _post(self, path, payload=None):
        return await self._request("POST", path, payload=payload, form=False)

    # ---------- 项目相关 ----------
    async def list_projects(self):
        return await self._get("/projects/list")

    async def find_project_code(self, project_name):
        """
        根据项目名称查找 projectCode。

        :param project_name: 项目名
        :return: 找到则返回 code (int), 未找到返回 None
        :raises Exception: 接口返回 code != 0 时
        """
        data = await self.list_projects()
        if data.get("code") != 0:
            raise Exception(f"查询项目失败: {data.get('msg')}")
        for project in data.get("data", []):
            if project.get("name") == project_name:
                return project.get("code")
        return None

    # ---------- 工作流定义/实例 ----------
    async def list_workflow_instances(self, project_code, workflow_name=None):
        """
        分页查询工作流实例列表。

        :param project_code: 项目 code
        :param workflow_name: 工作流名 (模糊匹配), 不传则查所有
        :return: 接口原始 JSON, 实际列表在 data.totalList
        """
        params = {
            "pageNo": 1,
            "pageSize": 10,
            "searchVal": workflow_name or "",
        }
        return await self._get(
            f"/projects/{project_code}/workflow-instances", params=params
        )

    async def get_workflow_instance(self, project_code, instance_id):
        """查询单个工作流实例详情。GET /projects/{projectCode}/workflow-instances/{id}"""
        return await self._get(
            f"/projects/{project_code}/workflow-instances/{instance_id}"
        )

    async def list_workflow_definition(self, project_code, search_val=None):
        """
        查询流程定义列表 (用于把工作流名换算成 workflowDefinitionCode)。

        GET /projects/{projectCode}/workflow-definition
        """
        params = {
            "pageNo": 1,
            "pageSize": 10,
            "searchVal": search_val or "",
        }
        return await self._get(
            f"/projects/{project_code}/workflow-definition", params=params
        )

    # ---------- 执行/控制 ----------
    async def start_workflow_instance(self, project_code, workflow_definition_code, **kwargs):
        """
        触发工作流执行。

        POST /projects/{projectCode}/executors/start-workflow-instance
        注意: DS 该接口要求 form-urlencoded 而非 JSON, 因此走
        _request(..., form=True) 而不是 _post。
        """
        data = {
            "workflowDefinitionCode": workflow_definition_code,
            "execType": "START_PROCESS",
            "scheduleTime": "",
            "failureStrategy": "CONTINUE",
            "warningType": "NONE",
            "workflowInstancePriority": "MEDIUM",
            "workerGroup": "default",
            **kwargs,
        }
        return await self._request(
            "POST",
            f"/projects/{project_code}/executors/start-workflow-instance",
            payload=data,
            form=True,
        )

    async def control_workflow_instance(self, project_code, workflow_instance_id, execute_type):
        """
        控制工作流实例 (停止/暂停/恢复等)。

        POST /projects/{projectCode}/executors/execute
        :param execute_type: STOP / PAUSE / RESUME / REPEAT_RUNNING 等
        """
        payload = {
            "workflowInstanceId": workflow_instance_id,
            "executeType": execute_type,
        }
        return await self._post(
            f"/projects/{project_code}/executors/execute", payload
        )

    async def get_instance_tasks(self, project_code, instance_id):
        """
        查询指定工作流实例下的任务列表。

        GET /projects/{projectCode}/workflow-instances/{id}/tasks
        :return: 任务列表在 data.taskList
        """
        return await self._get(
            f"/projects/{project_code}/workflow-instances/{instance_id}/tasks"
        )

    async def find_task_in_instances(self, project_code, workflow_name, task_name):
        """
        在最近若干个工作流实例中查找指定任务名的任务。

        思路: DS 任务查询必须指定 instanceId, 而用户通常只记得任务名。
        这里先按工作流名反查最近 N 个实例, 再逐个查其任务列表, 找到名字匹配的。

        :param project_code: 项目 code
        :param workflow_name: 工作流名
        :param task_name: 任务名
        :return: (instance_info, task_info) 或 (None, None)
        """
        # 1. 拉最近一批工作流实例
        instances_result = await self.list_workflow_instances(project_code, workflow_name)
        if instances_result.get("code") != 0:
            raise Exception(f"查询工作流实例失败: {instances_result.get('msg')}")

        total_list = instances_result.get("data", {}).get("totalList", [])
        if not total_list:
            return None, None

        # 2. 逐个实例查任务, 找到名字匹配的就返回
        for inst in total_list[:TASK_SEARCH_INSTANCE_LIMIT]:
            instance_id = inst.get("id")
            if not instance_id:
                continue

            tasks_result = await self.get_instance_tasks(project_code, instance_id)
            if tasks_result.get("code") != 0:
                continue

            task_list = tasks_result.get("data", []).get('taskList')
            for task in task_list:
                if task.get("name") == task_name:
                    return inst, task

        return None, None
TASK_SEARCH_INSTANCE_LIMIT = 5
