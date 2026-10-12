DATABASES = {
    'BI': {
        'conn_type': 'mysql',
        'host': '127.0.0.1',
        'username': '',
        'password': '',
        'port': '9030',
        'db_name': ''
    },
    # 任意新增条目都会作为 sqlbot 模块的 db_name：
    # 例: 钉钉发送 "sql 查询 BI select 1"
}

# 钉钉机器人白名单改用项目根目录下的 dingtalk_allowed_users.txt 文件管理，
# 每行一个 senderId (= staffId)，# 开头为注释，文件可热加载无需重启。
# 如需自定义路径，在 settings.py 中设置:
# DINGTALK_ALLOWED_USERS_FILE = "/path/to/your/whitelist.txt"


DS_BASE_URL = "http://your-dolphinscheduler-host:12345/dolphinscheduler"
DS_TOKEN = ""


DING_APP_ID = 'dingxxxxxxx'
DING_APP_SECRET = ''

# ---------- dolphinscheduler.scheduler_hard_reset 排除名单 ----------
# 任务名列表: SQL `name NOT IN (...)`，例如 ['ods-crm']
HARD_RESET_EXCLUDE_TASKS = []
# SQL LIKE 模式列表，多条用 AND 连接，例如 ['%ods-cdc%']
HARD_RESET_EXCLUDE_WORKFLOW_PATTERNS = []


# ---------- SeaTunnel ----------
SEATUNNEL_BASE_URL = "http://your-seatunnel-host:8080"
SEATUNNEL_CLUSTER_NAME = "default"
# 显示名 → jobId 映射，留空则只能用原始 jobId
# 例: {"etl_order_sync": "1111111111111111111"}
SEATUNNEL_JOB_ID_MAP = {}
