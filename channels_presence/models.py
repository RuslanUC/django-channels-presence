from __future__ import annotations
from datetime import timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.timezone import now

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from channels_presence.signals import presence_changed

channel_layer = get_channel_layer()


class PresenceManager(models.Manager):
    def touch(self, channel_name):
        self.filter(channel_name=channel_name).update(last_seen=now())

    async def touch_async(self, channel_name):
        await self.filter(channel_name=channel_name).aupdate(last_seen=now())

    def leave_all(self, channel_name):
        for presence in self.select_related("room", "user").filter(channel_name=channel_name):
            room = presence.room
            room.remove_presence(presence=presence)

    async def leave_all_async(self, channel_name):
        async for presence in self.select_related("room", "user").filter(channel_name=channel_name):
            room = presence.room
            await room.remove_presence_async(presence=presence)


class Presence(models.Model):
    room = models.ForeignKey("Room", on_delete=models.CASCADE)
    channel_name = models.CharField(max_length=255, help_text="Reply channel for connection that is present")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.CASCADE)
    last_seen = models.DateTimeField(default=now)

    objects = PresenceManager()

    def __str__(self):
        return self.channel_name

    class Meta:
        unique_together = [("room", "channel_name")]


class RoomManager(models.Manager):
    def add(self, room_channel_name, user_channel_name, user=None):
        room, created = Room.objects.get_or_create(channel_name=room_channel_name)
        room.add_presence(user_channel_name, user)
        return room

    async def add_async(self, room_channel_name, user_channel_name, user=None) -> Room:
        room, created = await Room.objects.aget_or_create(channel_name=room_channel_name)
        await room.add_presence_async(user_channel_name, user)
        return room

    def remove(self, room_channel_name, user_channel_name):
        try:
            room = Room.objects.get(channel_name=room_channel_name)
        except ObjectDoesNotExist:
            return
        room.remove_presence(user_channel_name)

    async def remove_async(self, room_channel_name, user_channel_name):
        try:
            room = await Room.objects.aget(channel_name=room_channel_name)
        except ObjectDoesNotExist:
            return
        await room.remove_presence_async(user_channel_name)

    def prune_presences(self, channel_layer=None, age=None):
        for room in Room.objects.all():
            room.prune_presences(age)

    async def prune_presences_async (self, channel_layer=None, age=None):
        async for room in Room.objects.all():
            await room.prune_presences_async(age)

    def prune_rooms(self):
        Room.objects.filter(presence__isnull=True).delete()

    async def prune_rooms_async(self):
        await Room.objects.filter(presence__isnull=True).adelete()


class Room(models.Model):
    channel_name = models.CharField(max_length=255, unique=True, help_text="Group channel name for this room")

    objects = RoomManager()

    def __str__(self):
        return self.channel_name

    def add_presence(self, channel_name, user=None):
        if user and user.is_authenticated:
            authed_user = user
        else:
            authed_user = None
        presence, created = Presence.objects.get_or_create(
            room=self, channel_name=channel_name, user=authed_user
        )
        if created:
            async_to_sync(channel_layer.group_add)(self.channel_name, channel_name)
            self.broadcast_changed(added=presence)

    async def add_presence_async(self, channel_name, user=None):
        if user and user.is_authenticated:
            authed_user = user
        else:
            authed_user = None
        presence, cr = await Presence.objects.aget_or_create(room=self, channel_name=channel_name, user=authed_user)
        if cr:
            await channel_layer.group_add(self.channel_name, channel_name)
            self.broadcast_changed(added=presence)

    def remove_presence(self, channel_name=None, presence=None):
        if presence is None:
            try:
                presence = Presence.objects.get(room=self, channel_name=channel_name)
            except ObjectDoesNotExist:
                return

        async_to_sync(channel_layer.group_discard)(
            self.channel_name, presence.channel_name
        )
        presence.delete()
        self.broadcast_changed(removed=presence)

    async def remove_presence_async(self, channel_name=None, presence=None):
        if presence is None:
            try:
                presence = await Presence.objects.aget(room=self, channel_name=channel_name)
            except ObjectDoesNotExist:
                return

        await channel_layer.group_discard(self.channel_name, presence.channel_name)
        await presence.adelete()
        self.broadcast_changed(removed=presence)

    def prune_presences(self, age_in_seconds=None):
        if age_in_seconds is None:
            age_in_seconds = getattr(settings, "CHANNELS_PRESENCE_MAX_AGE", 60)

        num_deleted, num_per_type = Presence.objects.filter(
            room=self, last_seen__lt=now() - timedelta(seconds=age_in_seconds)
        ).delete()
        if num_deleted > 0:
            self.broadcast_changed(bulk_change=True)

    async def prune_presences_async(self, age_in_seconds=None):
        if age_in_seconds is None:
            age_in_seconds = getattr(settings, "CHANNELS_PRESENCE_MAX_AGE", 60)

        num_deleted, num_per_type = await Presence.objects.filter(
            room=self, last_seen__lt=now() - timedelta(seconds=age_in_seconds)
        ).adelete()
        if num_deleted > 0:
            self.broadcast_changed(bulk_change=True)

    def get_users(self):
        User = get_user_model()
        return User.objects.filter(presence__room=self).distinct()

    def get_anonymous_count(self):
        return self.presence_set.filter(user=None).count()

    async def get_anonymous_count_async(self):
        return self.presence_set.filter(user=None).count()

    def broadcast_changed(self, added=None, removed=None, bulk_change=False):
        presence_changed.send(
            sender=self.__class__,
            room=self,
            added=added,
            removed=removed,
            bulk_change=bulk_change,
        )
