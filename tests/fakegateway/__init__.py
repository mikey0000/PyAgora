"""An in-process stand-in for Agora's gateway, access points and RTM REST API (docs/testing.md §6)."""

from tests.fakegateway.server import FakeAgora
from tests.fakegateway.state import DevicePublisher, Edge, FakeAgoraState, JoinRejection

__all__ = ["DevicePublisher", "Edge", "FakeAgora", "FakeAgoraState", "JoinRejection"]
