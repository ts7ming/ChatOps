"""
tools/print_user_id.py
======================

监听钉钉机器人消息，实时打印发送者的 senderId 和昵称，并自动去重追加到
项目根目录的 dingtalk_allowed_users.txt（也即机器人鉴权用的白名单文件），
无需重启服务即可生效。

用法:
    1. 确保 settings.py 已配置 DING_APP_ID / DING_APP_SECRET
    2. 启动本工具:  python tools/print_user_id.py
    3. 让目标用户在钉钉里向机器人发送任意消息 (如 "hi")
    4. 控制台立刻打印 id 与昵称，并自动追加到 dingtalk_allowed_users.txt
    5. Ctrl+C 退出

依赖: pip install dingtalk-stream
"""
import json
import logging
import os
import sys

import dingtalk_stream
from dingtalk_stream import AckMessage

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import settings  # noqa: E402
from dingtalk.dingtalk_helper import ALLOWED_USERS_FILE  # noqa: E402


NAMES_FILE = ALLOWED_USERS_FILE + ".names"  # 旁路保存 id→昵称，仅供参考
HEADER = (
    "# 钉钉机器人白名单\n"
    "# 每行一个 senderId (= staffId)，以 # 开头的行视为注释\n"
    "# 本文件可随时修改，机器人会按 mtime 自动重载，无需重启\n"
)


def setup_logger():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _normalize_id(line):
    """
    从一行文本中提取出真正的 senderId。
    兼容:
        STAFF123
        STAFF123  # 张三
        张三(STAFF123)
    """
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    raw = raw.split("#", 1)[0].strip()
    if not raw:
        return None
    if "(" in raw and raw.endswith(")"):
        raw = raw.rsplit("(", 1)[-1][:-1].strip()
    return raw or None


def _read_existing_ids(path):
    if not os.path.exists(path):
        return set()
    ids = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            sid = _normalize_id(line)
            if sid:
                ids.add(sid)
    return ids


def _append_id(path, names_path, sender_id, sender_nick):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(HEADER)
    # 白名单文件只写裸 id，避免行内注释被当作 id 的一部分
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{sender_id}\n")
    # 昵称单独存到 .names 文件（id\t昵称），仅作参考不影响鉴权
    if sender_nick:
        with open(names_path, "a", encoding="utf-8") as f:
            f.write(f"{sender_id}\t{sender_nick}\n")


class UserIdCollector(dingtalk_stream.ChatbotHandler):
    def __init__(self):
        super().__init__()
        self.seen = _read_existing_ids(ALLOWED_USERS_FILE)

    def _extract(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                return None, None, None
        if not isinstance(data, dict):
            return None, None, None
        sender_id = data.get("senderId") or data.get("senderStaffId")
        sender_nick = data.get("senderNick", "未知")
        text = ((data.get("text") or {}).get("content") or "").strip()
        return sender_id, sender_nick, text

    async def process(self, callback: dingtalk_stream.CallbackMessage):
        try:
            sender_id, sender_nick, text = self._extract(callback.data)
            if not sender_id:
                print(f"[!] 解析不到 senderId, raw={callback.data!r}", flush=True)
                return AckMessage.STATUS_OK, "OK"

            print("-" * 60, flush=True)
            if sender_id not in self.seen:
                self.seen.add(sender_id)
                _append_id(ALLOWED_USERS_FILE, NAMES_FILE, sender_id, sender_nick)
                print(f"[新] 昵称: {sender_nick}", flush=True)
                print(f"     id  : {sender_id}", flush=True)
                print(f"     已追加 → {ALLOWED_USERS_FILE}", flush=True)
                print(f"     昵称记录 → {NAMES_FILE}", flush=True)
            else:
                print(f"[已知] {sender_nick}({sender_id})  消息: {text}", flush=True)
        except Exception as e:
            print(f"[!] 处理失败: {e}", flush=True)
        return AckMessage.STATUS_OK, "OK"


def main():
    setup_logger()
    if not getattr(settings, "DING_APP_ID", None) or not getattr(settings, "DING_APP_SECRET", None):
        print("请先在 settings.py 中配置 DING_APP_ID / DING_APP_SECRET", flush=True)
        sys.exit(1)

    credential = dingtalk_stream.Credential(
        client_id=settings.DING_APP_ID,
        client_secret=settings.DING_APP_SECRET,
    )
    client = dingtalk_stream.DingTalkStreamClient(credential)
    client.register_callback_handler(
        dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
        UserIdCollector(),
    )
    print("=" * 60, flush=True)
    print("监听中, 请让目标用户在钉钉里向机器人发送任意消息 (Ctrl+C 退出)", flush=True)
    print(f"白名单文件: {ALLOWED_USERS_FILE}", flush=True)
    print("=" * 60, flush=True)
    try:
        client.start_forever()
    except KeyboardInterrupt:
        print("\n已退出", flush=True)


if __name__ == "__main__":
    main()
