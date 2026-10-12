import asyncio
import logging

from pyqueen import DataSource
from settings import DATABASES


logger = logging.getLogger(__name__)


class SqlbotHelper:
    """
    钉钉指令格式:
        sql 查询 <db_name> <sql...>   # 读操作（select / show / desc ...）
        sql 执行 <db_name> <sql...>   # 写操作（insert / update / delete / DDL ...）

    说明:
        - db_name 必须是 settings.DATABASES 中定义的 key，例如 'BI'、'etl-config'
        - 查询使用 ds.read_sql(sql)；执行使用 ds.exe_sql(sql, auto_commit=True)
        - 每次请求都会新建一个 DataSource，操作完关闭连接
    """

    ACTIONS_QUERY = ("查询", "查询sql", "query", "select", "read", "sql")
    ACTIONS_EXEC = ("执行", "执行sql", "exec", "execute", "run", "write")

    # 单次返回最多展示的行数，防止大结果集刷屏
    MAX_DISPLAY_ROWS = 100

    def __init__(self, databases=None):
        self.databases = databases if databases is not None else (DATABASES or {})

    # ----------------- helpers -----------------
    def _resolve_cfg(self, db_name):
        cfg = self.databases.get(db_name)
        if not cfg:
            raise ValueError(
                f"未知 db_name: '{db_name}'。"
                f"请确认已在 settings.DATABASES 中定义。可用: {list(self.databases.keys())}"
            )
        return cfg

    @staticmethod
    def _format_dataframe(df):
        """DataFrame → 可读表格文本，钉钉消息可直接显示"""
        if df is None:
            return "（无数据）"
        try:
            n_total = len(df)
        except Exception:
            return str(df)

        if n_total == 0:
            return "（无数据）"

        truncated = n_total > SqlbotHelper.MAX_DISPLAY_ROWS
        if truncated:
            df = df.head(SqlbotHelper.MAX_DISPLAY_ROWS)

        try:
            body = df.to_markdown(index=False)
        except Exception:
            body = df.to_string(index=False)

        if truncated:
            body += f"\n... 仅展示前 {SqlbotHelper.MAX_DISPLAY_ROWS} 行，共 {n_total} 行"
        return body

    async def _with_ds(self, db_name, op):
        """
        在线程池中调用 pyqueen（其 API 是同步的），并确保关闭连接。
        op 接收 ds，返回结果。
        """
        cfg = self._resolve_cfg(db_name)
        ds = DataSource(**cfg)
        try:
            return await asyncio.to_thread(op, ds)
        finally:
            try:
                await asyncio.to_thread(ds.close_conn)
            except Exception:
                pass

    # ----------------- actions -----------------
    async def _query(self, db_name, sql):
        df = await self._with_ds(db_name, lambda ds: ds.read_sql(sql))
        body = self._format_dataframe(df)
        return f"查询成功 ({db_name})\n{body}"

    async def _exec(self, db_name, sql):
        result = await self._with_ds(
            db_name, lambda ds: ds.exe_sql(sql, auto_commit=True)
        )
        return f"执行成功 ({db_name}): {result}"

    # ----------------- entry -----------------
    @staticmethod
    def _help():
        return (
            "支持的指令：\n"
            "1. 查询 <db_name> <sql>    （select 等读操作，返回 DataFrame）\n"
            "2. 执行 <db_name> <sql>    （insert/update/delete/DDL）\n"
            "db_name 必须是 settings.DATABASES 中的 key。"
        )

    async def process(self, cmd):
        try:
            if not cmd:
                return self._help()

            action = cmd[0]
            if len(cmd) < 3:
                return f"格式错误。正确格式: {action} <db_name> <sql>\n{self._help()}"

            db_name = cmd[1]
            sql = " ".join(cmd[2:]).strip()
            if not sql:
                return "SQL 不能为空。"

            if action in self.ACTIONS_QUERY:
                return await self._query(db_name, sql)
            if action in self.ACTIONS_EXEC:
                return await self._exec(db_name, sql)
            return self._help()
        except Exception as e:
            logger.exception("sqlbot 处理失败")
            return f"处理失败: {e}"
