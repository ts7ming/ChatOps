import json
import logging
import os

import dingtalk_stream
from dingtalk_stream import AckMessage

# 默认白名单文件：项目根目录下的 dingtalk_allowed_users.txt
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ALLOWED_USERS_FILE = os.path.join(_PROJECT_ROOT, "dingtalk_allowed_users.txt")

try:
    from settings import DINGTALK_ALLOWED_USERS_FILE
except Exception:
    DINGTALK_ALLOWED_USERS_FILE = None


def _resolve_allowed_users_file():
    """优先用 settings.DINGTALK_ALLOWED_USERS_FILE，否则用项目根的默认文件"""
    if DINGTALK_ALLOWED_USERS_FILE:
        return os.path.abspath(DINGTALK_ALLOWED_USERS_FILE)
    return DEFAULT_ALLOWED_USERS_FILE


class DingtalkHelper(dingtalk_stream.ChatbotHandler):
    """
    钉钉消息入口。

    白名单文件 dingtalk_allowed_users.txt 位于项目根，每行一个 senderId (= staffId)，
    以 # 开头的行为注释。文件被修改后无需重启服务，下次消息进来会自动重读（按 mtime 缓存）。
    """

    def __init__(self, handler_map, allowed_users_file=None):
        super(dingtalk_stream.ChatbotHandler, self).__init__()
        self.handler_map = handler_map
        self.allowed_users_file = (
            os.path.abspath(allowed_users_file)
            if allowed_users_file
            else _resolve_allowed_users_file()
        )
        # 缓存文件 mtime + 解析结果，避免每条消息都读盘
        self._cached_mtime = -1.0
        self._cached_users = frozenset()

    def _load_allowed_users(self):
        """
        读取白名单文件，按 mtime 缓存。
        - 文件不存在或读取失败 → 返回空集合（拒绝所有，安全默认）
        - 文件存在 → 每行一个 id，跳过空行与以 # 开头的注释行
        """
        try:
            mtime = os.path.getmtime(self.allowed_users_file)
        except OSError:
            self._cached_mtime = -1.0
            self._cached_users = frozenset()
            return self._cached_users

        if mtime == self._cached_mtime:
            return self._cached_users

        users = set()
        try:
            with open(self.allowed_users_file, "r", encoding="utf-8") as f:
                for line in f:
                    raw = line.strip()
                    if not raw or raw.startswith("#"):
                        continue
                    # 兼容写法:
                    #   "STAFF123"               → 整行作为 id
                    #   "STAFF123  # 张三"       → 截到第一个 # 前
                    #   "张三(STAFF123)"         → 提取括号内的 id
                    #   "张三(STAFF123)  # 备注" → 先去注释再取括号
                    sender_id = raw.split("#", 1)[0].strip()
                    if "(" in sender_id and sender_id.endswith(")"):
                        sender_id = sender_id.rsplit("(", 1)[-1][:-1].strip()
                    if sender_id:
                        users.add(sender_id)
        except OSError:
            users = set()

        self._cached_mtime = mtime
        self._cached_users = frozenset(users)
        return self._cached_users

    def _check_permission(self, callback):
        allowed = self._load_allowed_users()
        has_permission = False
        sender_id = "未知"
        sender_nick = "未知"

        data = callback.data
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = None
        if isinstance(data, dict):
            sender_id = data.get("senderId") or data.get("senderStaffId") or "未知"
            sender_nick = data.get("senderNick", "未知")

        if sender_id in allowed:
            has_permission = True

        result = "已授权" if has_permission else "未授权"
        self.logger.warning(
            f"用户: {sender_nick}({sender_id}) ---- {result} "
            f"[白名单文件: {self.allowed_users_file}, 共 {len(allowed)} 人]"
        )
        return has_permission

    async def process(self, callback: dingtalk_stream.CallbackMessage):
        incoming_message = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
        content = incoming_message.text.content.strip()
        self.logger.info(f"收到消息: {content}")
        if self._check_permission(callback) is False:
            self.reply_text("仅接收授权用户指令", incoming_message)
            return AckMessage.STATUS_OK, 'OK'

        reply_text = ""
        try:
            content_list = content.split()
            if not content_list:
                reply_text = "请输入指令，例如: ds 查询 项目名"
            else:
                project = content_list[0]
                project_cmd = content_list[1:]
                handler = self.handler_map.get(project)
                if handler is None:
                    reply_text = (
                        f"未知功能前缀: '{project}'。"
                        f"可用: {list(self.handler_map.keys())}"
                    )
                else:
                    reply_text = await handler.process(project_cmd)
        except Exception as e:
            self.logger.exception("处理消息时发生错误")
            reply_text = f"处理失败: {str(e)}"

        self.reply_text(reply_text, incoming_message)
        return AckMessage.STATUS_OK, 'OK'
