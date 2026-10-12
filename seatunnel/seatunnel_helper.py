from datetime import datetime

from settings import (
    SEATUNNEL_BASE_URL,
    SEATUNNEL_CLUSTER_NAME,
    SEATUNNEL_JOB_ID_MAP,
)
from seatunnel.seatunnel_base import SeatunnelBase


class SeatunnelHelper(SeatunnelBase):
    """
    钉钉指令格式: st <动作> [参数]
        st 查询
        st 暂停 <jobId或名称>
        st 保存 <jobId或名称>     (保存 checkpoint / 触发 savepoint)
        st 恢复 <jobId或名称>     (从 savepoint 恢复)
        st 取消 <jobId或名称>     (直接停止，不保留 savepoint)
    """

    QUERY_LIMIT = 5
    FINISHED_STATES = ("FINISHED", "CANCELED", "FAILED", "UNKNOWABLE")

    def __init__(
        self,
        base_url=None,
        cluster_name=None,
        job_id_map=None,
    ):
        super().__init__(
            base_url=base_url or SEATUNNEL_BASE_URL,
            cluster_name=cluster_name if cluster_name is not None else SEATUNNEL_CLUSTER_NAME,
        )
        self.job_id_map = job_id_map if job_id_map is not None else (SEATUNNEL_JOB_ID_MAP or {})

    def _resolve_job_id(self, name_or_id):
        """支持友好名 → jobId 映射，找不到则原样返回"""
        if not name_or_id:
            return name_or_id
        return self.job_id_map.get(name_or_id, name_or_id)

    @staticmethod
    def _ok(result, success_msg):
        """
        SeaTunnel 响应形如 {success: bool, message: ..., data: ...}
        成功返回 success_msg；失败返回 message
        """
        if isinstance(result, dict):
            if result.get("success") is False:
                return f"失败: {result.get('message') or result.get('msg') or result}"
        return success_msg

    @staticmethod
    def _extract_jobs(data):
        """兼容多种返回结构，提取 job 列表"""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("jobs", "data", "list"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
                if isinstance(value, dict):
                    for sub_key in ("jobs", "data", "list"):
                        if isinstance(value.get(sub_key), list):
                            return value[sub_key]
        return []

    @staticmethod
    def _parse_time(value):
        """把时间字段解析为可比较的时间戳，无法解析时返回 0"""
        if value is None:
            return 0
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if not text:
            return 0
        if text.isdigit():
            return float(text)
        text = text.replace("T", " ").replace("Z", "").split(".")[0].split("+")[0].strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt).timestamp()
            except ValueError:
                continue
        return 0

    @classmethod
    def _finished_ts(cls, job):
        if not isinstance(job, dict):
            return 0
        return cls._parse_time(
            job.get("finishedTime") or job.get("finishTime") or job.get("createTime")
        )

    @staticmethod
    def _format_job(job):
        if not isinstance(job, dict):
            return f"- ID: {job} | 状态: 未知"
        jid = job.get("jobId") or job.get("job_id") or "?"
        name = job.get("jobName") or job.get("job_name") or jid
        status = job.get("jobStatus") or job.get("status") or "未知"
        line = f"- {name} | ID: {jid} | 状态: {status}"
        finished_time = job.get("finishedTime") or job.get("finishTime")
        if finished_time and status != "RUNNING":
            line += f" | 完成时间: {finished_time}"
        return line

    async def _recent_finished_jobs(self, limit):
        """取最近执行的已完成 job（各结束状态合并后按结束时间倒序）"""
        jobs = []
        for state in self.FINISHED_STATES:
            try:
                data = await self.list_finished_jobs(state)
            except Exception:
                continue
            jobs.extend(self._extract_jobs(data))
        jobs.sort(key=self._finished_ts, reverse=True)
        return jobs[:limit]

    async def _list_jobs(self):
        """列出运行中的 5 个 job，不足 5 个时用最近执行的 job 补齐"""
        running = self._extract_jobs(await self.list_running_jobs())

        lines = [self._format_job(j) for j in running[:self.QUERY_LIMIT]]
        if len(running) < self.QUERY_LIMIT:
            recent = await self._recent_finished_jobs(self.QUERY_LIMIT - len(running))
            lines.extend(self._format_job(j) for j in recent)

        if not lines:
            return "当前没有运行中或最近执行的 job。"

        if len(running) > self.QUERY_LIMIT:
            return f"job 列表 (运行中 {len(running)} 个，展示 {self.QUERY_LIMIT} 个)：\n" + "\n".join(lines)
        return f"job 列表 (共 {len(lines)} 个)：\n" + "\n".join(lines)

    async def _pause(self, job_id):
        result = await self.stop_job(job_id, with_save_point=True)
        return self._ok(result, f"已暂停 job '{job_id}' (已保存 savepoint)")

    async def _savepoint(self, job_id):
        result = await self.savepoint_job(job_id)
        sp_path = None
        if isinstance(result, dict):
            sp_path = result.get("savepointPath") or result.get("data", {}).get("savepointPath")
        msg = f"已触发 job '{job_id}' 的 checkpoint"
        if sp_path:
            msg += f"\n路径: {sp_path}"
        return self._ok(result, msg)

    async def _restore(self, job_id):
        result = await self.restore_job(job_id)
        return self._ok(result, f"已从 savepoint 恢复 job '{job_id}'")

    async def _cancel(self, job_id):
        result = await self.stop_job(job_id, with_save_point=False)
        return self._ok(result, f"已取消 job '{job_id}'")

    @staticmethod
    def _help():
        return (
            "支持的指令：\n"
            "1. 查询\n"
            "2. 暂停 [jobId或名称]\n"
            "3. 保存 [jobId或名称]    (保存 checkpoint)\n"
            "4. 恢复 [jobId或名称]\n"
            "5. 取消 [jobId或名称]\n"
        )

    async def process(self, cmd):
        reply_text = ""
        try:
            if not cmd:
                return self._help()

            action = cmd[0]
            args = cmd[1:]

            if action == '查询':
                return await self._list_jobs()

            if not args:
                return f"格式错误。请使用: {action} [jobId或名称]"

            job_id = self._resolve_job_id(args[0])

            if action == '暂停':
                return await self._pause(job_id)
            if action in ('保存', '保存checkpoint', 'checkpoint'):
                return await self._savepoint(job_id)
            if action in ('恢复', '恢复执行', 'resume'):
                return await self._restore(job_id)
            if action in ('取消', '停止', 'cancel'):
                return await self._cancel(job_id)

            return self._help()

        except Exception as e:
            return f"处理失败: {str(e)}"
