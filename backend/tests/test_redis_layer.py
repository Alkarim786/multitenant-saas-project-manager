"""Against real Redis: the in-memory layer used elsewhere can't show these failures."""

import asyncio
import os

import pytest
import redis
from channels_redis.core import RedisChannelLayer
from django.conf import settings

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_REDIS"), reason="set TEST_REDIS=1 to run against real Redis")


def make_layer():
    return RedisChannelLayer(**settings.REDIS_LAYER_CONFIG)


@pytest.mark.asyncio
async def test_idle_read_outlasts_the_blocking_pop():
    """Regression: an idle socket crashed with redis TimeoutError after about 5 seconds."""
    layer = make_layer()
    channel = await layer.new_channel()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(layer.receive(channel), timeout=8)
    except redis.exceptions.TimeoutError:
        pytest.fail("redis socket timed out during an idle read")
    finally:
        await layer.close_pools()


@pytest.mark.asyncio
async def test_group_message_is_delivered():
    layer = make_layer()
    channel = await layer.new_channel()
    await layer.group_add("board_test", channel)
    await layer.group_send("board_test", {"type": "board.event", "event": "issue.moved", "payload": {"id": 1}})
    message = await asyncio.wait_for(layer.receive(channel), timeout=5)
    assert message["payload"] == {"id": 1}
    await layer.group_discard("board_test", channel)
    await layer.close_pools()
