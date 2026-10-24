"""Standard-library WSGI instrumentation; no body buffering or inline delivery."""

import logging
import threading

log = logging.getLogger(__name__)


class _Input:
    def __init__(self, stream, environ):
        self.stream = stream
        self.received = 0
        self.eof = False
        try:
            self.length = int(environ.get("CONTENT_LENGTH", ""))
        except (ValueError, TypeError):
            self.length = None
        if self.length is not None and self.length < 0:
            self.length = None
        self.terminated = environ.get("wsgi.input_terminated", False)
        self.empty = environ.get("REQUEST_METHOD") in ("GET", "HEAD") and not (
            environ.get("CONTENT_LENGTH") or environ.get("HTTP_TRANSFER_ENCODING")
        )

    @property
    def complete(self):
        return self.empty or self.length == self.received or (self.terminated and self.eof)

    def _read(self, method, size=-1):
        data = getattr(self.stream, method)(size)
        self.received += len(data)
        if size != 0 and (not data or (method == "read" and size < 0)):
            self.eof = True
        return data

    def read(self, size=-1):
        return self._read("read", size)

    def readline(self, size=-1):
        return self._read("readline", size)

    def readlines(self, hint=-1):
        lines, total = [], 0
        while line := self.readline():
            lines.append(line)
            total += len(line)
            if hint > 0 and total >= hint:
                break
        return lines

    def readinto(self, buffer):
        count = self.stream.readinto(buffer)
        if count is not None:
            self.received += count
            if len(buffer) and count == 0:
                self.eof = True
        return count

    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line

    def __getattr__(self, name):
        return getattr(self.stream, name)


class _Response:
    def __init__(self, environ, monitor):
        self.environ = environ
        self.input = _Input(environ["wsgi.input"], environ)
        environ["wsgi.input"] = self.input
        self.context = monitor.request()
        self.result = self.context.__enter__()
        self.status = 500
        self.sent = 0
        self.finished = False
        self.closed = False

    def finish(self, *, failed=False, complete=False):
        if self.finished:
            return
        self.finished = True
        self.result.status = 500 if failed else self.status
        # Applications may publish a registered route template after dispatch.
        # Never derive labels from PATH_INFO, query strings or request contents.
        route = self.environ.get("bobowatch.route")
        self.result.route = route if isinstance(route, str) else "unmatched"
        if complete and self.input.complete:
            self.result.bytes_received = self.input.received
            self.result.bytes_sent = self.sent
        self.context.__exit__(None, None, None)

    def __iter__(self):
        return self

    def __next__(self):
        try:
            data = next(self.iterator)
            self.sent += len(data)
            return data
        except StopIteration:
            self.finish(complete=True)
            raise
        except BaseException:
            self.finish(failed=True)
            raise

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            close = getattr(self.iterable, "close", None)
            if close:
                close()
        except BaseException:
            self.finish(failed=True)
            raise
        finally:
            self.finish()


class InstrumentedApp:
    """Start one monitor after fork, then close after serving stops.

    The application sets environ['bobowatch.route'] to its resolved template.
    Only templates registered on the Monitor become distinct metric labels.
    Unknown/partially consumed bodies and interrupted responses remain unsized.
    Server-level rejections before WSGI dispatch cannot be observed here.
    """

    def __init__(self, app, factory):
        self.app, self.factory = app, factory
        self.monitor = None
        self._lock = threading.Lock()
        self._attempted = False

    def start(self):
        with self._lock:
            if not self._attempted:
                self._attempted = True
                monitor = None
                try:
                    monitor = self.factory()
                    monitor.start()
                    self.monitor = monitor
                except Exception as error:
                    if monitor:
                        monitor.close()
                    log.warning("Monitoring unavailable (%s); app continues", type(error).__name__)
            return self.monitor

    def close(self):
        with self._lock:
            if self.monitor:
                self.monitor.close()
                self.monitor = None
            self._attempted = True

    def __call__(self, environ, start_response):
        monitor = self.start()
        if monitor is None:
            return self.app(environ, start_response)
        response = _Response(environ, monitor)

        def measured_start(status, headers, exc_info=None):
            write = start_response(status, headers, exc_info)
            response.status = int(status.split()[0])

            def measured_write(data):
                write(data)
                response.sent += len(data)

            return measured_write

        try:
            response.iterable = self.app(environ, measured_start)
            response.iterator = iter(response.iterable)
            return response
        except BaseException:
            response.finish(failed=True)
            raise
