"""``AgoraSession``: one gateway WebSocket session, from join to close (``docs/architecture.md`` §2).

Composes the pure layers (``sdp``, ``session.messages``, ``session.recovery``) with a ``GatewayTransport``.
Every background task is owned (D13); every ending goes through ``_end`` and fires ``on_closed`` once (D14, D23).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import replace
from functools import partial
import logging
import time
from typing import TYPE_CHECKING

from pyagorartc.ap.response import fingerprints_from_edge
from pyagorartc.capture import capture
from pyagorartc.const import (
    DECLARED_SSRC_TIMEOUT_S,
    EDGE_DOMAIN_SUFFIX,
    GATEWAY_SEND_TIMEOUT_S,
    KEEPALIVE_INTERVAL_S,
    PING_INTERVAL_S,
)
from pyagorartc.exceptions import GatewayConnectError, JoinTimeoutError, SessionClosedError
from pyagorartc.models import CloseReason, IceCandidate, SessionOptions, fingerprint
from pyagorartc.sdp import answer_from_ortc, candidates_to_ortc, extract_inline_candidates, offer_to_ortc
from pyagorartc.session.messages import (
    JOIN_ROLE,
    FrameType,
    build_join,
    build_leave,
    build_ping,
    build_renew_token,
    build_set_client_role,
    build_subscribe,
    build_unsubscribe,
    describe_frame,
    encode_frame,
    is_quit,
    new_process_id,
    new_request_id,
    parse_error,
    parse_frame,
    parse_join_result,
    parse_notification,
    parse_p2p_lost,
    parse_p2p_ok,
    parse_remote_stream,
    parse_rtp_capability_change,
    parse_user_event,
)
from pyagorartc.session.recovery import Keepalive, PeerRecovery, PingWatchdog, RenewDebounce
from pyagorartc.session.transport import WebsocketsTransport

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

    from pyagorartc.ap.response import APResponse
    from pyagorartc.models import ChannelCredentials, EdgeAddress, RemoteStream
    from pyagorartc.session.messages import GatewayFrame, JoinResult, JsonObject
    from pyagorartc.session.transport import GatewayConnection, GatewayTransport

_LOGGER = logging.getLogger(__name__)

# shipped (HA-Luba ``_send_set_client_role``; protocol.md §3.3)
_CLIENT_ROLE_LEVEL = 0

type Spawn = Callable[[Coroutine[object, object, None]], asyncio.Task[None]]


class AgoraSession:
    """One offer, one join, one close (architecture §2); a new offer is a new session.

    ``deadline`` is absolute seconds on ``clock``; ``sleep`` must measure the same clock (tests pass a
    manual pair). ``spawn`` replaces the task factory for every background task (D13). Host callbacks
    run in owned tasks; an exception they raise is logged, never propagated into the session.
    """

    def __init__(  # noqa: PLR0913 - every host hook and seam is explicit and keyword-only
        self,
        creds: ChannelCredentials,
        ap: APResponse,
        *,
        options: SessionOptions = SessionOptions(),  # noqa: B008 - frozen dataclass, safe as a default
        transport: GatewayTransport | None = None,
        token_provider: Callable[[], Awaitable[str | None]] | None = None,
        on_peer_left: Callable[[int], Awaitable[None]] | None = None,
        on_closed: Callable[[CloseReason], Awaitable[None]] | None = None,
        on_stream: Callable[[RemoteStream], Awaitable[None]] | None = None,
        keepalive: Callable[[], Awaitable[bool]] | None = None,
        keepalive_interval_s: float = KEEPALIVE_INTERVAL_S,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        spawn: Spawn | None = None,
        request_id_factory: Callable[[], str] = new_request_id,
    ) -> None:
        self._creds = creds
        self._ap = ap
        self._options = options
        self._transport: GatewayTransport = transport if transport is not None else WebsocketsTransport()
        self._token_provider = token_provider
        self._on_peer_left = on_peer_left
        self._on_closed = on_closed
        self._on_stream = on_stream
        self._keepalive = keepalive
        self._keepalive_policy = Keepalive(interval_s=keepalive_interval_s, deadline=deadline)
        self._clock = clock
        self._wall_clock = wall_clock
        self._sleep = sleep
        self._spawn_factory = spawn
        self._new_id = request_id_factory
        self._recovery = PeerRecovery(clock=clock)
        self._renew_debounce = RenewDebounce(window_s=options.renew_debounce_s, clock=clock)
        self._ping = PingWatchdog(clock=clock)

        self._candidates: list[IceCandidate] = []
        self._tasks: set[asyncio.Task[None]] = set()
        self._pending: dict[str, asyncio.Future[GatewayFrame]] = {}
        self._join_waiter: asyncio.Future[GatewayFrame] | None = None
        self._conn: GatewayConnection | None = None
        self._edge: EdgeAddress | None = None
        self._reader_stopped = False
        self._join_started = False
        self._joined = False
        self._pending_end: CloseReason | None = None
        self._own_uid = creds.uid
        self._token = creds.token
        self._rtx = False
        self._online: set[int] = set()
        self._streams: dict[tuple[int, int], RemoteStream] = {}
        self._subscriptions: dict[tuple[int, int], asyncio.Task[None]] = {}
        self._stream_announced = asyncio.Event()
        self._recovery_task: asyncio.Task[None] | None = None
        self._unknown_types: set[str] = set()
        self._close_reason: CloseReason | None = None
        self._ender: asyncio.Task[object] | None = None
        self._ended = asyncio.Event()

    def __repr__(self) -> str:
        return f"AgoraSession(channel={self._creds.channel_name!r}, uid={self._creds.uid}, state={self._state})"

    @property
    def _state(self) -> str:
        if self._close_reason is not None:
            return f"closed:{self._close_reason.value}"
        if self._joined:
            return "joined"
        return "joining" if self._join_started else "new"

    @property
    def is_connected(self) -> bool:
        """Whether the gateway socket is open and the session has not ended."""
        return self._close_reason is None and self._conn is not None and self._conn.is_open

    @property
    def is_joined(self) -> bool:
        """Whether the join succeeded and the session has not ended."""
        return self._joined and self._close_reason is None

    @property
    def remote_users(self) -> frozenset[int]:
        """Publishers in the channel (uids other than ours that pass ``target_uid``)."""
        return frozenset(self._online)

    @property
    def remote_streams(self) -> tuple[RemoteStream, ...]:
        """Video streams announced (in the join payload or since) that pass ``target_uid``, in arrival order."""
        return tuple(self._streams.values())

    @property
    def close_reason(self) -> CloseReason | None:
        """Why the session ended, or ``None`` while it runs."""
        return self._close_reason

    def add_ice_candidate(self, candidate: IceCandidate | str) -> None:
        """Queue a viewer candidate for the join ORTC; once ``join`` has started it is ignored (D11, D23, Q4)."""
        if self._join_started:
            _LOGGER.debug("Ignoring an ICE candidate added after join: the gateway has no trickle message (Q4)")
            return
        if isinstance(candidate, str):
            candidate = IceCandidate(candidate)
        self._candidates.append(replace(candidate, candidate=candidate.candidate.removeprefix("a=")))

    async def join(self, offer_sdp: str, session_id: str) -> str:
        """Join the channel and return the answer SDP for ``offer_sdp``.

        Raises:
            SessionClosedError: ``join`` was already called, or the session ended while joining.
            GatewayConnectError: The AP named no gateway edge or none accepted a connection, the socket took no
                join frame within ``GATEWAY_SEND_TIMEOUT_S``, or it closed before the result.
            JoinRejectedError: The gateway refused the join.
            JoinTimeoutError: No join result within ``options.join_timeout_s``.
            SdpError: The offer or the gateway ORTC could not be translated.

        On any failure the socket is closed, owned tasks are cancelled and ``on_closed`` fires before the
        exception propagates: ``JOIN_FAILED``, or ``GATEWAY_QUIT`` / ``P2P_LOST`` when the gateway ended the
        session before ``join`` finished, which raises ``GatewayConnectError`` (D9, D23, D29).

        """
        if self._join_started or self._close_reason is not None:
            raise SessionClosedError("an AgoraSession joins once; create a new session for a new offer")
        self._join_started = True
        try:
            return await self._join(offer_sdp, session_id)
        except BaseException:
            await self._end(self._pending_end or CloseReason.JOIN_FAILED)
            raise

    async def renew_token(self, token: str | None = None) -> None:
        """Send ``renew_token``: ``token``, else ``token_provider()``, else the last token sent (D8).

        Raises:
            SessionClosedError: The session is not joined.
            GatewayConnectError: The socket is gone.

        """
        if not self.is_joined:
            raise SessionClosedError("renew_token needs a joined session")
        if token is None and self._token_provider is not None:
            token = await self._token_provider()
        if token and token != self._token:
            self._token = token
            _LOGGER.info("Channel token rotated for %s (fingerprint %s)", self._creds.channel_name, fingerprint(token))
        await self._send(build_renew_token(self._token, request_id=self._new_id()))

    async def close(self) -> None:
        """End the session: cancel and await every owned task, ``leave`` when joined (bounded), close the socket.

        Idempotent and safe after a failed join or from inside ``on_closed`` (it never awaits its own task).
        """
        if self._close_reason is None:
            await self._end(CloseReason.CLOSED_BY_HOST)
            return
        current = asyncio.current_task()
        if current is self._ender:
            return
        await self._ended.wait()
        if others := [task for task in self._tasks if task is not current]:
            await asyncio.gather(*others, return_exceptions=True)

    async def _join(self, offer_sdp: str, session_id: str) -> str:
        ortc = offer_to_ortc(offer_sdp, dtls_role=self._options.ortc_dtls_role)
        ortc["iceParameters"]["candidates"] = candidates_to_ortc(
            _dedupe([*extract_inline_candidates(offer_sdp), *self._candidates])
        )
        self._edge, conn = await self._connect()
        if self._close_reason is not None:
            await conn.close()
            self._ensure_running()
        self._conn = conn
        self._spawn(self._message_loop(conn))

        request_id = self._new_id()
        waiter: asyncio.Future[GatewayFrame] = asyncio.get_running_loop().create_future()
        self._join_waiter = waiter
        self._pending[request_id] = waiter
        timer = self._spawn(self._expire_join(waiter))
        try:
            await self._send_bounded(
                build_join(
                    self._creds,
                    ortc,
                    self._ap.to_ap_response(),
                    options=self._options,
                    session_id=session_id,
                    process_id=new_process_id(),
                    client_ts_ms=self._wall_ms(),
                    request_id=request_id,
                )
            )
            frame = await waiter
        finally:
            self._pending.pop(request_id, None)
            self._join_waiter = None
            timer.cancel()
        join = parse_join_result(frame)
        if self._pending_end is not None:
            # Checked first: the gateway usually closes the socket right after the quit it announced.
            raise GatewayConnectError(f"gateway ended the session during join ({self._pending_end.value})")
        if self._reader_stopped:
            # The result arrived, but the message loop stopped before this task resumed to act on it.
            raise GatewayConnectError("gateway socket closed before the join completed")
        _LOGGER.debug("Joined channel %s as uid %s (cid %s)", self._creds.channel_name, join.uid, self._ap.cid)
        await self._on_joined(join)

        remote_video = None
        if self._options.declare_remote_video_ssrc:
            remote_video = await self._wait_for_remote_video()
        return answer_from_ortc(
            self._with_edge_fingerprint(join.ortc), offer_sdp, options=self._options, remote_video=remote_video
        )

    async def _on_joined(self, join: JoinResult) -> None:
        self._joined = True
        self._own_uid = join.uid if join.uid is not None else self._creds.uid
        self._rtx = join.offers_rtx
        self._recovery.reset()
        # Presence seen before the result named our uid.
        self._online.discard(self._own_uid)
        for key in [k for k in self._streams if k[0] == self._own_uid]:
            del self._streams[key]
        if self._options.send_set_client_role:
            # D6: off by default; Mammotion mowers leave the channel when they see it.
            await self._send(
                build_set_client_role(
                    JOIN_ROLE, _CLIENT_ROLE_LEVEL, client_ts_ms=self._wall_ms(), request_id=self._new_id()
                )
            )
            self._ensure_running()
        for stream in join.existing_streams:
            if self._wanted(stream.uid) and stream.uid != self._own_uid:
                self._online.add(stream.uid)
                self._announce(stream)
        for stream in list(self._streams.values()):
            self._maybe_subscribe(stream)
        self._spawn(self._ping_loop())
        if self._keepalive is not None or self._keepalive_policy.deadline is not None:
            self._spawn(self._keepalive_loop())

    def _with_edge_fingerprint(self, ortc: Mapping[str, object]) -> Mapping[str, object]:
        """D26: when the gateway ORTC carries no fingerprint, the AP's for the connected edge (else any gateway edge)."""
        dtls = ortc.get("dtlsParameters", {})
        if not isinstance(dtls, Mapping) or _has_fingerprint(dtls):
            return ortc
        for edge in [*([self._edge] if self._edge is not None else []), *self._ap.get_gateway_addresses()]:
            if fingerprints := fingerprints_from_edge(edge):
                _LOGGER.debug("Gateway ORTC has no DTLS fingerprint; using the AP's for edge %s", edge.ip)
                return {**ortc, "dtlsParameters": {**dtls, "fingerprints": fingerprints}}
        return ortc

    async def _connect(self) -> tuple[EdgeAddress, GatewayConnection]:
        if not (edges := self._ap.get_gateway_addresses()):
            raise GatewayConnectError("the access point returned no gateway edge")
        if offset := self._options.gateway_edge_offset % len(edges):
            edges = edges[offset:] + edges[:offset]
        for edge in edges:
            url = _edge_url(edge)
            try:
                return edge, await self._transport.connect(
                    url, timeout_s=self._options.connect_timeout_s, verify_ssl=self._options.verify_ssl
                )
            except GatewayConnectError as exc:
                _LOGGER.debug("Gateway edge %s refused the connection: %s", url, exc)
        raise GatewayConnectError(f"no gateway edge accepted a connection ({len(edges)} tried)")

    async def _expire_join(self, waiter: asyncio.Future[GatewayFrame]) -> None:
        await self._sleep(self._options.join_timeout_s)
        if not waiter.done():
            waiter.set_exception(JoinTimeoutError(f"no join result within {self._options.join_timeout_s}s"))

    async def _wait_for_remote_video(self) -> RemoteStream | None:
        if not self._streams:
            timer = self._spawn(self._expire_stream_wait())
            try:
                await self._stream_announced.wait()
            finally:
                timer.cancel()
            self._ensure_running()
        if not self._streams:
            _LOGGER.debug(
                "No video stream announced within %ss; answering without a remote SSRC", DECLARED_SSRC_TIMEOUT_S
            )
            return None
        return next(iter(self._streams.values()))

    async def _expire_stream_wait(self) -> None:
        await self._sleep(DECLARED_SSRC_TIMEOUT_S)
        self._stream_announced.set()

    async def _message_loop(self, conn: GatewayConnection) -> None:
        """Read until the socket closes; however the loop stops (a handler raising included), a joined session ends."""
        try:
            while self._close_reason is None:
                try:
                    text = await conn.recv()
                except GatewayConnectError:
                    return
                capture("gateway", "in", text)
                if (frame := parse_frame(text)) is None:
                    _LOGGER.debug("Ignoring a gateway frame that is not a JSON object")
                    continue
                self._ping.frame_received()
                await self._dispatch(frame)
        finally:
            self._reader_stopped = True
            if (waiter := self._join_waiter) is not None and not waiter.done():
                waiter.set_exception(GatewayConnectError("gateway socket closed before the join result"))
            if self._joined and self._close_reason is None:
                await self._end(CloseReason.SOCKET_CLOSED)

    async def _dispatch(self, frame: GatewayFrame) -> None:
        if frame.id is not None:
            if (future := self._pending.pop(frame.id, None)) is not None and not future.done():
                future.set_result(frame)
            elif self._ping.reply(frame.id, ok=frame.ok):
                if not frame.ok:
                    _LOGGER.debug("Gateway answered a ping with result %s", frame.result)
            else:
                _LOGGER.debug("Response matches no pending request (%s): %s", frame.result, describe_frame(frame))
            return
        if (handler := _HANDLERS.get(frame.type or "")) is None:
            if (name := frame.type or "<none>") not in self._unknown_types:
                self._unknown_types.add(name)
                _LOGGER.debug("Ignoring unhandled gateway frame: %s", describe_frame(frame))
            return
        await handler(self, frame)

    async def _on_add_video_stream(self, frame: GatewayFrame) -> None:
        if (stream := parse_remote_stream(frame.message)) is None:
            _LOGGER.debug("Ignoring on_add_video_stream without an int uid and ssrcId")
            return
        if stream.uid == self._own_uid or not self._wanted(stream.uid):
            _LOGGER.debug(
                "Ignoring a stream from uid %s (ours %s, target %s)",
                stream.uid,
                self._own_uid,
                self._options.target_uid,
            )
            return
        if not self._options.subscribe_requires_online:
            # D28: the announcement stands in for presence, so a later on_user_offline still tears it down.
            self._online.add(stream.uid)
        self._announce(stream)
        self._maybe_subscribe(stream)

    async def _on_user_online(self, frame: GatewayFrame) -> None:
        if (event := parse_user_event(frame.message)) is None:
            _LOGGER.debug("Ignoring on_user_online without a uid")
            return
        if event.uid == self._own_uid or not self._wanted(event.uid):
            return
        self._online.add(event.uid)
        for stream in [s for s in self._streams.values() if s.uid == event.uid]:
            self._maybe_subscribe(stream)

    async def _on_user_offline(self, frame: GatewayFrame) -> None:
        if (event := parse_user_event(frame.message)) is None:
            _LOGGER.debug("Ignoring on_user_offline without a uid")
            return
        uid = event.uid
        if uid == self._own_uid or not self._wanted(uid):
            return
        _LOGGER.debug("Peer %s left the channel (reason %s)", uid, event.reason)
        self._online.discard(uid)
        for key in [k for k in self._streams if k[0] == uid]:
            del self._streams[key]
        if subscriptions := [self._subscriptions.pop(k) for k in list(self._subscriptions) if k[0] == uid]:
            for task in subscriptions:
                task.cancel()
            with suppress(GatewayConnectError):
                await self._send(build_unsubscribe(uid, request_id=self._new_id()))
        if self._on_peer_left is None or not self.is_joined:
            return
        if self._recovery_task is not None:
            self._recovery_task.cancel()
            self._recovery_task = None
        if (delay := self._recovery.peer_left(uid)) is not None:
            self._recovery_task = self._spawn(self._recover_peer(uid, delay))

    async def _recover_peer(self, uid: int, delay: float) -> None:
        await self._sleep(delay)
        # Past the debounce a later departure must not cancel the host's on_peer_left mid-call.
        if self._recovery_task is asyncio.current_task():
            self._recovery_task = None
        if not self.is_joined or self._on_peer_left is None:
            return
        if self._recovery.should_recover(uid, peer_present=uid in self._online):
            await self._run_callback("on_peer_left", self._on_peer_left(uid))

    async def _on_token_will_expire(self, _frame: GatewayFrame) -> None:
        if self.is_joined and self._renew_debounce.should_send():
            self._spawn(self._renew_on_expiry())

    async def _renew_on_expiry(self) -> None:
        try:
            await self.renew_token()
        except (GatewayConnectError, SessionClosedError):
            self._renew_debounce.clear()
            _LOGGER.debug("renew_token could not be sent; the next will_expire retries")
        except Exception:
            self._renew_debounce.clear()
            raise

    async def _on_token_did_expire(self, _frame: GatewayFrame) -> None:
        _LOGGER.warning("Gateway reports the channel token expired for %s", self._creds.channel_name)
        self._renew_debounce.clear()

    async def _on_notification(self, frame: GatewayFrame) -> None:
        notification = parse_notification(frame.message)
        if not is_quit(notification):
            _LOGGER.debug("Gateway notification %s (code %s)", notification.action, notification.code)
            return
        _LOGGER.warning(
            "Gateway quit the session on %s (code %s, %s)",
            self._creds.channel_name,
            notification.code,
            notification.detail,
        )
        await self._end_by_gateway(CloseReason.GATEWAY_QUIT)

    async def _on_p2p_lost(self, frame: GatewayFrame) -> None:
        lost = parse_p2p_lost(frame)
        if not self._options.end_on_p2p_lost:
            # D22: Mammotion ignored it on purpose; Q13 asks what it means for a subscriber.
            _LOGGER.debug("Gateway reported p2p_lost (code %s, %s); ignored", lost.code, lost.error)
            return
        _LOGGER.warning("Gateway reported p2p_lost (code %s, %s); ending the session", lost.code, lost.error)
        await self._end_by_gateway(CloseReason.P2P_LOST)

    async def _end_by_gateway(self, reason: CloseReason) -> None:
        """End a joined session; while ``join`` is in flight, leave the ending for ``join`` to apply (D29)."""
        if self._joined:
            await self._end(reason)
            return
        self._pending_end = self._pending_end or reason
        if (waiter := self._join_waiter) is not None and not waiter.done():
            waiter.set_exception(GatewayConnectError(f"gateway ended the session during join ({reason.value})"))

    async def _on_p2p_ok(self, frame: GatewayFrame) -> None:
        ok = parse_p2p_ok(frame.message)
        if ok.uid is not None and ok.uid != self._own_uid:
            _LOGGER.debug("p2p_ok names uid %s, not ours (%s)", ok.uid, self._own_uid)

    async def _on_rtp_capability_change(self, frame: GatewayFrame) -> None:
        caps = parse_rtp_capability_change(frame.message)
        _LOGGER.debug("Gateway RTP capabilities changed: video codecs %s", caps.video_codecs)

    async def _on_error(self, frame: GatewayFrame) -> None:
        error = parse_error(frame)
        _LOGGER.warning("Gateway error event (code %s): %s", error.code, error.message)

    def _announce(self, stream: RemoteStream) -> None:
        self._streams.setdefault((stream.uid, stream.ssrc), stream)
        self._stream_announced.set()

    def _maybe_subscribe(self, stream: RemoteStream) -> None:
        # Subscribe once both the stream and its publisher are known; they arrive in either order (protocol.md §3.2).
        key = (stream.uid, stream.ssrc)
        if not self.is_joined or key in self._subscriptions:
            return
        if stream.uid not in self._online:
            _LOGGER.debug("Holding stream %s from uid %s until on_user_online (Q18)", stream.ssrc, stream.uid)
            return
        self._subscriptions[key] = self._spawn(self._subscribe(stream))

    async def _subscribe(self, stream: RemoteStream) -> None:
        key = (stream.uid, stream.ssrc)
        attempts = self._options.subscribe_retry_attempts
        for attempt in range(attempts + 1):
            if attempt > 0 and self._subscriptions.get(key) is not asyncio.current_task():
                return
            request_id = self._new_id()
            ack: asyncio.Future[GatewayFrame] = asyncio.get_running_loop().create_future()
            self._pending[request_id] = ack
            try:
                await self._send(
                    build_subscribe(stream, codec=self._options.client_codec, rtx=self._rtx, request_id=request_id)
                )
            except GatewayConnectError:
                self._pending.pop(request_id, None)
                _LOGGER.debug(
                    "Subscribe to uid %s could not be sent; the message loop reports the closed socket", stream.uid
                )
                return
            if attempt == 0 and self._on_stream is not None:
                await self._run_callback("on_stream", self._on_stream(stream))
            if attempt == attempts:
                # D31: the last attempt's ack is tracked, not awaited; it stays pending until answered or the end.
                ack.add_done_callback(partial(_report_subscribe_ack, stream.uid))
                return
            await self._sleep(self._options.subscribe_retry_delay_s)
            self._pending.pop(request_id, None)
            if ack.done() and not ack.cancelled() and ack.result().ok:
                return
            ack.cancel()
            _LOGGER.debug("Subscribe to uid %s not acknowledged; retry %s/%s", stream.uid, attempt + 1, attempts)

    async def _ping_loop(self) -> None:
        while True:
            await self._sleep(PING_INTERVAL_S)
            if self._ping.tick():
                _LOGGER.warning(
                    "Gateway on %s answered no ping for %s ticks and sent nothing for over %ss; ending the session",
                    self._creds.channel_name,
                    self._ping.unanswered,
                    self._ping.silence_s,
                )
                await self._end(CloseReason.PING_TIMEOUT)
                return
            request_id = self._new_id()
            self._ping.sent(request_id)
            try:
                await self._send(build_ping(request_id))
            except GatewayConnectError:
                _LOGGER.debug("Ping could not be sent; the message loop reports the closed socket")
                return

    async def _keepalive_loop(self) -> None:
        policy = self._keepalive_policy
        keepalive = self._keepalive
        while self._close_reason is None:
            if policy.deadline_reached(self._clock()):
                _LOGGER.info("Session deadline reached for %s; ending the stream", self._creds.channel_name)
                await self._end(CloseReason.DEADLINE)
                return
            if keepalive is not None and await self._run_callback("keepalive", keepalive()) is False:
                _LOGGER.debug("Keep-alive callback returned False; keep-alive stopped")
                keepalive = None
            now = self._clock()
            if keepalive is not None:
                await self._sleep(policy.delay(now))
            elif policy.deadline is not None:
                await self._sleep(max(0.0, policy.deadline - now))
            else:
                return

    async def _end(self, reason: CloseReason) -> None:
        if self._close_reason is not None:
            return
        self._close_reason = reason
        current = asyncio.current_task()
        self._ender = current
        _LOGGER.debug("Ending session on %s: %s", self._creds.channel_name, reason.value)
        if (waiter := self._join_waiter) is not None and not waiter.done():
            waiter.set_exception(SessionClosedError(f"session ended while joining ({reason.value})"))
        self._stream_announced.set()
        if others := [task for task in self._tasks if task is not current]:
            for task in others:
                task.cancel()
            await asyncio.gather(*others, return_exceptions=True)
        for future in self._pending.values():
            future.cancel()
        self._pending.clear()
        if (conn := self._conn) is not None:
            if reason is CloseReason.CLOSED_BY_HOST and self._joined and conn.is_open:
                with suppress(GatewayConnectError):
                    await self._send_bounded(build_leave(self._new_id()))
            with suppress(GatewayConnectError):
                await conn.close()
        self._online.clear()
        self._streams.clear()
        self._subscriptions.clear()
        try:
            if self._on_closed is not None:
                await self._run_callback("on_closed", self._on_closed(reason))
        finally:
            self._ended.set()

    async def _send(self, frame: JsonObject) -> None:
        if (conn := self._conn) is None:
            raise SessionClosedError("the session has no gateway socket")
        await conn.send(encode_frame(frame))
        capture("gateway", "out", frame)
        _LOGGER.debug("Sent %s", describe_frame(frame))

    async def _send_bounded(self, frame: JsonObject) -> None:
        """``_send`` within ``GATEWAY_SEND_TIMEOUT_S``; a stalled socket raises ``GatewayConnectError``."""
        try:
            async with asyncio.timeout(GATEWAY_SEND_TIMEOUT_S):
                await self._send(frame)
        except TimeoutError as exc:
            raise GatewayConnectError(f"gateway socket accepted no frame within {GATEWAY_SEND_TIMEOUT_S}s") from exc

    def _spawn(self, coro: Coroutine[object, object, None]) -> asyncio.Task[None]:
        if self._close_reason is not None:
            coro.close()
            raise SessionClosedError(f"session ended ({self._close_reason.value}); no new task is started")
        task = self._spawn_factory(coro) if self._spawn_factory else asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and (exc := task.exception()) is not None:
            _LOGGER.warning("Session task failed on %s", self._creds.channel_name, exc_info=exc)

    async def _run_callback[T](self, name: str, awaitable: Awaitable[T]) -> T | None:
        try:
            return await awaitable
        except Exception:  # noqa: BLE001 - a host callback must not break the session (D14)
            _LOGGER.warning("Host callback %s raised; ignored", name, exc_info=True)
            return None

    def _ensure_running(self) -> None:
        if self._close_reason is not None:
            raise SessionClosedError(f"session ended while joining ({self._close_reason.value})")

    def _wanted(self, uid: int) -> bool:
        return self._options.target_uid is None or uid == self._options.target_uid

    def _wall_ms(self) -> int:
        return int(self._wall_clock() * 1000)


def _report_subscribe_ack(uid: int, ack: asyncio.Future[GatewayFrame]) -> None:
    # D31: a refused subscribe is logged; the SDK rejects only that request and keeps the session.
    if ack.cancelled() or (frame := ack.result()).ok:
        return
    error = parse_error(frame)
    _LOGGER.warning("Gateway refused the subscribe to uid %s (code %s, %s)", uid, error.code, error.message)


def _edge_url(edge: EdgeAddress) -> str:
    return f"wss://{edge.ip.replace('.', '-')}{EDGE_DOMAIN_SUFFIX}:{edge.port}"


def _has_fingerprint(dtls: Mapping[str, object]) -> bool:
    fingerprints = dtls.get("fingerprints")
    return any(
        isinstance(entry, Mapping) and entry.get("fingerprint")
        for entry in (fingerprints if isinstance(fingerprints, list) else [])
    )


def _dedupe(candidates: list[IceCandidate]) -> list[IceCandidate]:
    seen: set[str] = set()
    unique = []
    for candidate in candidates:
        if candidate.candidate not in seen:
            seen.add(candidate.candidate)
            unique.append(candidate)
    return unique


type _Handler = Callable[[AgoraSession, GatewayFrame], Awaitable[None]]

_HANDLERS: Mapping[str, _Handler] = {
    FrameType.ON_ADD_VIDEO_STREAM: AgoraSession._on_add_video_stream,  # noqa: SLF001
    FrameType.ON_USER_ONLINE: AgoraSession._on_user_online,  # noqa: SLF001
    FrameType.ON_USER_OFFLINE: AgoraSession._on_user_offline,  # noqa: SLF001
    FrameType.ON_TOKEN_PRIVILEGE_WILL_EXPIRE: AgoraSession._on_token_will_expire,  # noqa: SLF001
    FrameType.ON_TOKEN_PRIVILEGE_DID_EXPIRE: AgoraSession._on_token_did_expire,  # noqa: SLF001
    FrameType.ON_NOTIFICATION: AgoraSession._on_notification,  # noqa: SLF001
    FrameType.ON_P2P_LOST: AgoraSession._on_p2p_lost,  # noqa: SLF001
    FrameType.ON_P2P_OK: AgoraSession._on_p2p_ok,  # noqa: SLF001
    FrameType.ON_RTP_CAPABILITY_CHANGE: AgoraSession._on_rtp_capability_change,  # noqa: SLF001
    FrameType.ERROR: AgoraSession._on_error,  # noqa: SLF001
}
