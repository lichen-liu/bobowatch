"""Dependency-free ASGI instrumentation, including streamed response completion."""

import asyncio
import logging
from contextlib import ExitStack

log = logging.getLogger(__name__)


class InstrumentedApp:
    """Construct/start a per-process Monitor from a factory during ASGI lifespan.

    The wrapped app owns routing, body consumption and error responses. This
    layer never reads an extra body, buffers a response or sends telemetry inline.
    """

    def __init__(self, app, factory):
        self.app, self.factory = app, factory
        self.monitor = None
        self.attempted = False

    def start(self):
        if not self.attempted:
            self.attempted = True
            try:
                self.monitor = self.factory()
                self.monitor.start()
            except Exception as error:
                self.monitor = None
                log.warning("Monitoring unavailable (%s); app continues", type(error).__name__)
        return self.monitor

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":

            async def lifespan_receive():
                message = await receive()
                if message["type"] == "lifespan.startup":
                    self.start()
                return message

            try:
                await self.app(scope, lifespan_receive, send)
            finally:
                if self.monitor:
                    await asyncio.to_thread(self.monitor.close)
                self.monitor, self.attempted = None, False
            return
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        monitor = self.start()
        if monitor is None:
            return await self.app(scope, receive, send)
        incoming = outgoing = 0
        request_complete = scope.get("method") in ("GET", "HEAD")
        response_complete = False
        status = 500

        async def measured_receive():
            nonlocal incoming, request_complete
            message = await receive()
            if message["type"] == "http.request":
                incoming += len(message.get("body", b""))
                request_complete = not message.get("more_body", False)
            return message

        async def measured_send(message):
            nonlocal outgoing, response_complete, status
            await send(message)
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                outgoing += len(message.get("body", b""))
                response_complete = not message.get("more_body", False)
                if response_complete:
                    result.status = status
                    result.route = getattr(scope.get("route"), "path", "unmatched")
                    if request_complete:
                        result.bytes_received, result.bytes_sent = incoming, outgoing
                    lifecycle.close()

        with ExitStack() as lifecycle:
            result = lifecycle.enter_context(monitor.request())
            try:
                await self.app(scope, measured_receive, measured_send)
            finally:
                result.status = status
                # Framework routing can resolve a template only after dispatch.
                result.route = getattr(scope.get("route"), "path", "unmatched")
                if request_complete and response_complete:
                    result.bytes_received, result.bytes_sent = incoming, outgoing
