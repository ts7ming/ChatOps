import json
import aiohttp


class SeatunnelBase:
    def __init__(self, base_url, cluster_name=None):
        self.base_url = base_url.rstrip('/')
        self.headers = {"Accept": "application/json"}
        if cluster_name:
            self.headers["Cluster-Name"] = cluster_name

    async def _request(self, method, path, params=None, payload=None):
        url = f"{self.base_url}{path}"
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, headers=self.headers, params=params, json=payload
            ) as resp:
                text = await resp.text()
                if text.strip().startswith('<'):
                    raise Exception(
                        f"SeaTunnel API 返回了 HTML 而非 JSON。状态码: {resp.status}, "
                        f"请检查路径 '{path}' 和 base_url/Cluster-Name 是否正确。"
                    )
                try:
                    return json.loads(text)
                except json.JSONDecodeError as e:
                    raise Exception(
                        f"JSON 解析失败。状态码: {resp.status}, 内容: {text[:500]}"
                    ) from e

    async def _get(self, path, params=None):
        return await self._request("GET", path, params=params)

    async def _post(self, path, payload=None):
        return await self._request("POST", path, payload=payload or {})

    # ---------- job 信息 ----------
    async def list_jobs(self):
        """列出所有 job (GET /jobs-overview)"""
        return await self._get("/jobs-overview")

    async def get_job_info(self, job_id):
        """查询 job 详情 (GET /job-info/{jobId})"""
        return await self._get(f"/job-info/{job_id}")

    async def list_running_jobs(self):
        """查询运行中的 job (GET /running-jobs)"""
        return await self._get("/running-jobs")

    async def list_finished_jobs(self, state):
        """
        查询已完成 job (GET /finished-jobs/{state})
        state 取值: FINISHED / CANCELED / FAILED / UNKNOWABLE
        """
        return await self._get(f"/finished-jobs/{state}")

    # ---------- job 控制 ----------
    async def submit_job(self, job_id, job_config=None):
        """提交 job (POST /submit-job)"""
        payload = {"jobId": job_id}
        if job_config is not None:
            payload["jobConfig"] = job_config
        return await self._post("/submit-job", payload)

    async def stop_job(self, job_id, with_save_point=False):
        """
        停止 job (POST /stop-job)
        with_save_point=True 时先 savepoint 再停止 (用于"暂停/可恢复停止")
        with_save_point=False 时直接取消
        """
        return await self._post(
            "/stop-job",
            {"jobId": job_id, "isStopWithSavePoint": bool(with_save_point)},
        )

    async def savepoint_job(self, job_id):
        """触发 savepoint (POST /savepoint-job)"""
        return await self._post("/savepoint-job", {"jobId": job_id})

    async def restore_job(self, job_id, savepoint_path=None):
        """从 savepoint 恢复 (POST /restore-job)"""
        payload = {"jobId": job_id}
        if savepoint_path:
            payload["savepointPath"] = savepoint_path
        return await self._post("/restore-job", payload)
