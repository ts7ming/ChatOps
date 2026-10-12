import json

import dingtalk_stream
from dingtalk_stream import AckMessage
try:
    from settings import DINGTALK_ALLOWED_USERS
except:
    DINGTALK_ALLOWED_USERS = None

class DingtalkHelper(dingtalk_stream.ChatbotHandler):
    def __init__(self, handler_map):
        super(dingtalk_stream.ChatbotHandler, self).__init__()
        self.handler_map = handler_map
        self.DINGTALK_ALLOWED_USERS =DINGTALK_ALLOWED_USERS

    def __check_permission(self, callback):
        has_permission = False
        sender_id = "未知"
        if self.DINGTALK_ALLOWED_USERS is not None:
            data = callback.data
            if isinstance(data, str):
                sender_id = json.loads(data).get("senderId", "")
                if sender_id in self.DINGTALK_ALLOWED_USERS:
                    has_permission = True
        else:
            has_permission = True
        result = "已授权" if has_permission else "未授权"
        self.logger.warning(f"用户: {sender_id} ---- {result}")
        return has_permission

    async def process(self, callback: dingtalk_stream.CallbackMessage):
        incoming_message = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
        content = incoming_message.text.content.strip()
        self.logger.info(f"收到消息: {content}")
        if self.__check_permission(callback) is False:
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