import json
import logging
import os

import dingtalk_stream
from dingtalk_stream import AckMessage

# 白名单文件：项目根目录下的 dingtalk_allowed_users.txt (固定路径)
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_USERS_FILE = os.path.join(_PROJECT_ROOT, "dingtalk_allowed_users.txt")

# 快捷指令文件：项目根目录下的 dingtalk_shortcuts.txt (固定路径, 可选)
SHORTCUTS_FILE = os.path.join(_PROJECT_ROOT, "dingtalk_shortcuts.txt")


class DingtalkHelper(dingtalk_stream.ChatbotHandler):
    """
    钉钉消息入口。

    白名单文件 dingtalk_allowed_users.txt 位于项目根，每行一个 senderId (= staffId)，
    以 # 开头的行为注释。文件被修改后无需重启服务，下次消息进来会自动重读（按 mtime 缓存）。

    快捷指令文件 dingtalk_shortcuts.txt (可选)，每行 "名称:命令"，机器人收到以名称
    开头的消息时会整行替换为对应命令后再路由。修改保存后自动重载。
    """

    def __init__(self, handler_map):
        super(dingtalk_stream.ChatbotHandler, self).__init__()
        self.handler_map = handler_map
        # 缓存文件 mtime + 解析结果，避免每条消息都读盘
        self._cached_mtime = -1.0
        self._cached_users = frozenset()
        self._shortcut_mtime = -1.0
        self._shortcuts = {}

    # 当白名单文件不存在时，_load_allowed_users 返回这个 sentinel，
    # 表示"放行所有消息"。文件存在但解析后为空则仍返回空 frozenset（拒绝所有）。
    _MISSING = object()

    def _load_allowed_users(self):
        """
        读取白名单文件，按 mtime 缓存。

        返回值:
            frozenset(...)   文件存在，按行解析出的白名单；空集合表示拒绝所有
            self._MISSING    文件不存在，放行所有
        """
        try:
            mtime = os.path.getmtime(ALLOWED_USERS_FILE)
        except OSError:
            # 文件不存在 → 放行所有
            self._cached_mtime = -1.0
            self._cached_users = self._MISSING
            return self._cached_users

        if mtime == self._cached_mtime:
            return self._cached_users

        users = set()
        try:
            with open(ALLOWED_USERS_FILE, "r", encoding="utf-8") as f:
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

    def _load_shortcuts(self):
        """
        读取快捷指令文件，按 mtime 缓存。
        文件不存在 → 返回空 dict（不启用快捷指令）。
        文件格式: 每行 "名称:命令"，'#' 开头为注释，空行跳过，名称禁止含空格。
        """
        try:
            mtime = os.path.getmtime(SHORTCUTS_FILE)
        except OSError:
            self._shortcut_mtime = -1.0
            self._shortcuts = {}
            return self._shortcuts

        if mtime == self._shortcut_mtime:
            return self._shortcuts

        shortcuts = {}
        try:
            with open(SHORTCUTS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    raw = line.strip()
                    if not raw or raw.startswith("#"):
                        continue
                    if ":" not in raw:
                        continue
                    name, _, value = raw.partition(":")
                    name = name.strip()
                    value = value.strip()
                    # 名称禁止含空格 (否则无法用 "首 token 匹配" 的方式路由)
                    if name and value and " " not in name and "\t" not in name:
                        shortcuts[name] = value
        except OSError:
            pass

        self._shortcut_mtime = mtime
        self._shortcuts = shortcuts
        return self._shortcuts

    def _expand_shortcut(self, content):
        """
        若 content 的第一个 token 是快捷指令键，整行替换为对应的命令。
        替换后只展开一次, 不递归 (避免 A→B→A 循环)。
        """
        self._load_shortcuts()  # 确保按 mtime 热加载最新内容
        parts = content.split(maxsplit=1)
        if not parts:
            return content
        first = parts[0]
        replacement = self._shortcuts.get(first)
        if replacement:
            self.logger.info(f"快捷指令 '{first}' 展开为: {replacement}")
            return replacement
        return content

    def _check_permission(self, callback):
        allowed = self._load_allowed_users()
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

        # 文件不存在 → 放行所有
        if allowed is self._MISSING:
            self.logger.warning(
                f"用户: {sender_nick}({sender_id}) ---- 已授权 "
                f"[白名单文件 {ALLOWED_USERS_FILE} 不存在，放行所有]"
            )
            return True

        has_permission = sender_id in allowed
        result = "已授权" if has_permission else "未授权"
        self.logger.warning(
            f"用户: {sender_nick}({sender_id}) ---- {result} "
            f"[白名单文件: {ALLOWED_USERS_FILE}, 共 {len(allowed)} 人]"
        )
        return has_permission

    async def process(self, callback: dingtalk_stream.CallbackMessage):
        incoming_message = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
        content = incoming_message.text.content.strip()
        self.logger.info(f"收到消息: {content}")
        if self._check_permission(callback) is False:
            self.reply_text("仅接收授权用户指令", incoming_message)
            return AckMessage.STATUS_OK, 'OK'

        # 快捷指令展开: 整行替换为文件中的命令 (不递归)
        content = self._expand_shortcut(content)

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
