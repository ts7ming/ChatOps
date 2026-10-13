DATABASES = {
    # DS 用于机器人强制操作 dolphinscheduler
    # 其他数据源可以自行添加
    'DS': {
        'conn_type': 'mysql',
        'host': '127.0.0.1',
        'username': '',
        'password': '',
        'port': '3306',
        'db_name': ''
    },
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
# 文件不存在 → 放行所有；文件存在但为空 → 拒绝所有。
# 路径固定在项目根，不可配置。

# ---------- ds 重置 配置 ----------
# 强重置使用的 DATABASES key (从上方 DATABASES 中选), 默认 'DS'
# HARD_RESET_DB = 'DS'
# 强重置 SQL 模板改用项目根目录的 ds_hard_reset_exclude.txt 文件, 按 mtime 热加载。
# 路径固定在项目根，不可配置。


DS_BASE_URL = "http://your-dolphinscheduler-host:12345/dolphinscheduler"
DS_TOKEN = ""


DING_APP_ID = 'dingxxxxxxx'
DING_APP_SECRET = ''

# ---------- SeaTunnel ----------
SEATUNNEL_BASE_URL = "http://your-seatunnel-host:8080"
SEATUNNEL_CLUSTER_NAME = "default"
