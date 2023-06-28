import functools

from channels_presence.models import Presence


def touch_presence(func):
    @functools.wraps(func)
    def inner(consumer, *args, **kwargs):
        Presence.objects.touch(consumer.channel_name)
        return func(consumer, *args, **kwargs)

    return inner


def touch_presence_async(func):
    @functools.wraps(func)
    async def inner(consumer, *args, **kwargs):
        await Presence.objects.touch_async(consumer.channel_name)
        return await func(consumer, *args, **kwargs)

    return inner


def remove_presence(func):
    @functools.wraps(func)
    def inner(consumer, *args, **kwargs):
        Presence.objects.leave_all(consumer.channel_name)
        return func(consumer, *args, **kwargs)

    return inner


def remove_presence_async(func):
    @functools.wraps(func)
    async def inner(consumer, *args, **kwargs):
        await Presence.objects.leave_all_async(consumer.channel_name)
        return await func(consumer, *args, **kwargs)

    return inner
