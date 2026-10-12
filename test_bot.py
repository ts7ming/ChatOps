import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from settings import DS_BASE_URL, DS_TOKEN
from dolphinscheduler.dolphin_helper import DolphinHelper
from seatunnel.seatunnel_helper import SeatunnelHelper
from sqlbot.sqlbot_helper import SqlbotHelper

# TEST_MESSAGE = "ds 查询 BI"
TEST_MESSAGE = 'sql 查询 bi select * from dwd.dim_store limit 4'

async def simulate(content):
    parts = content.split()
    prefix, cmd = parts[0], parts[1:]
    ds_helper = DolphinHelper(base_url=DS_BASE_URL, ds_token=DS_TOKEN)
    st_helper = SeatunnelHelper()
    sqlbot_helper = SqlbotHelper()

    handlers = {
        'ds': ds_helper,
        'st': st_helper,
        'sql': sqlbot_helper,
    }

    handler = handlers.get(prefix)
    if handler is None:
        return f"未知功能前缀: '{prefix}'。可用: {list(handlers.keys())}"
    return await handler.process(cmd)


async def main():
    print("=" * 60)
    print(f">>> {TEST_MESSAGE}")
    print("-" * 60)
    print(await simulate(TEST_MESSAGE))


if __name__ == '__main__':
    asyncio.run(main())
