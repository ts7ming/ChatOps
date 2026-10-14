import logging
import dingtalk_stream
from settings import DS_BASE_URL, DS_TOKEN, DING_APP_ID, DING_APP_SECRET
from dingtalk.dingtalk_helper import DingtalkHelper
from dolphinscheduler.dolphin_helper import DolphinHelper
from seatunnel.seatunnel_helper import SeatunnelHelper
from sqlbot.sqlbot_helper import SqlbotHelper


def setup_logger():
    logger = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(name)-8s %(levelname)-8s %(message)s [%(filename)s:%(lineno)d]'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


ds_helper = DolphinHelper(base_url=DS_BASE_URL, ds_token=DS_TOKEN)
st_helper = SeatunnelHelper()
sqlbot_helper = SqlbotHelper()

handler_map = {
    'ds': ds_helper,
    'st': st_helper,
    'sql': sqlbot_helper,
}


def main():
    logger = setup_logger()

    credential = dingtalk_stream.Credential(client_id=DING_APP_ID, client_secret=DING_APP_SECRET)
    client = dingtalk_stream.DingTalkStreamClient(credential)
    client.register_callback_handler(
        dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
        DingtalkHelper(handler_map)
    )

    logger.info("机器人启动，等待消息...")
    client.start_forever()



if __name__=='__main__':
    main()
