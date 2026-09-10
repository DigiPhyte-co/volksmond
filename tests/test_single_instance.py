"""Single-instance guard tests: one Volksmond, or none.

The bug (field report, 2026-08-27, a Mac): the app was already running, the tester
opened it again, and macOS started a SECOND fully independent copy - second Dock tile,
second window, second server. Two processes then competed for the same microphone, the
same GPU and the same sessions folder.

The cause was in this code, not in macOS: free_port() bound 127.0.0.1:8765, and on
OSError it silently bound port 0 instead and returned whatever the OS handed back. A
second launch therefore never noticed the first, it just quietly started its own server
somewhere else. The same defect shipped on Windows.

The fix is claim_port(): bind the fixed port, KEEP the socket, and hand that socket to
uvicorn. The kernel is then the arbitrator - exactly one process can hold a bound
listening socket for a port, and there is no check-then-act gap for two launches to slip
through. A launch that loses the bind probes the holder (GET /api/app-info, our own
existing endpoint) and either raises the running copy and exits 0, or, if the holder is
some unrelated program, steps aside onto a spare port so the app is never bricked.

Deliberately no lockfile: a stale lockfile after a crash locks the user out of their own
app, while a dead process releases its port, so the bound-port claim is self-healing.

Covered here: the claim is exclusive and self-healing; simultaneous claims have exactly
one winner; the probe recognises us, rejects a foreign service fast, and waits out an
instance that is still starting; main() hands over / falls back / starts normally; the
claimed socket reaches uvicorn unre-bound; the mac bundle carries
LSMultipleInstancesProhibited.

Not covered (needs a Mac): that LSMultipleInstancesProhibited stops the second launch,
that `open -b com.digiphyte.volksmond` raises the running window, and how either behaves
under Gatekeeper App Translocation.

Run:  python tests/test_single_instance.py   (from the project root; exit 0 = pass)
"""
import contextlib
import json
import os
import socket
import sys
import threading
import time
import types

# Make `import live_transcribe` work when run as a plain script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import live_transcribe.desktop as desktop

VOLKSMOND_JSON = json.dumps({"name": "Volksmond", "version": "1.13.2"}).encode()
FOREIGN_JSON = json.dumps({"name": "Some Other Dev Server"}).encode()


def _http(body=b"", status="200 OK"):
    """A complete, closeable HTTP/1.1 reply."""
    head = (f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n")
    return head.encode() + body


def _free_port():
    """A port number nothing is listening on right now."""
    s = socket.socket()
    s.bind((desktop.HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def _holder(reply=None, delay=0.0):
    """Hold a port the way another instance would, optionally answering HTTP on it.

    Yields the port. `reply=None` holds the port in silence (a listener that accepts
    nothing); `delay` starts answering only after that many seconds, which is what a
    Volksmond whose uvicorn has not attached yet looks like from the outside: the
    connection is accepted out of the kernel backlog and the request simply waits.
    """
    sock = desktop.claim_port(0)
    assert sock is not None, "could not bind a stub port"
    sock.settimeout(0.2)
    stop = threading.Event()

    def _serve():
        if delay:
            time.sleep(delay)
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except (socket.timeout, TimeoutError):
                continue
            except OSError:
                return
            try:
                conn.recv(65536)
                conn.sendall(reply)
            except OSError:
                pass
            finally:
                conn.close()

    thread = None
    if reply is not None:
        thread = threading.Thread(target=_serve, daemon=True, name="stub-instance")
        thread.start()
    try:
        yield sock.getsockname()[1]
    finally:
        stop.set()
        if thread is not None:
            thread.join(2.0)
        sock.close()


@contextlib.contextmanager
def _guard_env(port):
    """Point main()'s guard at `port` and stub out everything past the decision point.

    Yields a record of what main() did: the socket start_server was handed, and the
    handoff (url, mode) if it handed over. No uvicorn, no window, no browser - the guard
    itself (claim_port, is_running_instance, spare_socket) runs for real.
    """
    rec = {"served": None, "fronted": None}
    names = ("PREFERRED_PORT", "start_server", "wait_for_server", "_keep_alive",
             "front_running_instance")
    saved = {n: getattr(desktop, n) for n in names}
    try:
        desktop.PREFERRED_PORT = port

        def _start(sock):
            rec["served"] = sock
            return types.SimpleNamespace(should_exit=False)

        def _front(url, mode="window"):
            rec["fronted"] = (url, mode)
            return "stub"

        desktop.start_server = _start
        desktop.wait_for_server = lambda *a, **k: True
        desktop._keep_alive = lambda *a, **k: None
        desktop.front_running_instance = _front
        yield rec
    finally:
        for name, value in saved.items():
            setattr(desktop, name, value)
        if rec["served"] is not None:
            rec["served"].close()


def test_the_claim_is_exclusive_and_self_healing():
    """One holder at a time, and the port comes back when the holder goes away.

    The self-healing half is why there is no lockfile: a crashed instance releases its
    port, so the next launch just claims it."""
    first = desktop.claim_port(0)
    assert first is not None
    port = first.getsockname()[1]
    try:
        assert desktop.claim_port(port) is None, (
            f"a second claim of {port} succeeded; two instances would both think they won")
    finally:
        first.close()
    again = desktop.claim_port(port)
    assert again is not None, "the port did not come back after the holder released it"
    again.close()


def test_simultaneous_claims_have_exactly_one_winner():
    """The TOCTOU regression: twelve launches at once, one winner.

    The old free_port() bound the port, closed it and returned the number, leaving a gap
    before uvicorn re-bound it in which several launches could all see the port free."""
    port = _free_port()
    ready = threading.Barrier(12)
    won = []
    lock = threading.Lock()

    def _race():
        ready.wait(5)
        sock = desktop.claim_port(port)
        if sock is not None:
            with lock:
                won.append(sock)

    threads = [threading.Thread(target=_race, daemon=True, name=f"claim-{i}")
               for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    try:
        assert len(won) == 1, f"{len(won)} launches won the same port; exactly one may"
    finally:
        for sock in won:
            sock.close()


def test_a_running_instance_is_recognised():
    """The holder answers /api/app-info as Volksmond, so this launch must stand down."""
    with _holder(_http(VOLKSMOND_JSON)) as port:
        assert desktop.is_running_instance(desktop.HOST, port) is True


def test_a_foreign_service_is_not_mistaken_for_us():
    """Something else on 8765 must be classified as foreign, and quickly: the user is
    waiting, and the app has to start anyway."""
    for reply, label in ((_http(b'{"detail":"Not Found"}', "404 Not Found"), "an HTTP error"),
                         (_http(FOREIGN_JSON), "another JSON service"),
                         (_http(b"<html>hello</html>"), "a plain web page")):
        with _holder(reply) as port:
            t0 = time.monotonic()
            verdict = desktop.is_running_instance(desktop.HOST, port)
            elapsed = time.monotonic() - t0
        assert verdict is False, f"{label} was mistaken for a running Volksmond"
        assert elapsed < 5.0, f"{label} took {elapsed:.1f}s to classify; it answered at once"


def test_the_probe_waits_out_an_instance_that_is_still_starting():
    """Two launches milliseconds apart. The winner has the socket bound and listening but
    its uvicorn has not attached yet, so the loser's request is accepted and then waits.
    The loser must NOT read that silence as "not Volksmond" and start a second app."""
    t0 = time.monotonic()
    with _holder(_http(VOLKSMOND_JSON), delay=1.0) as port:
        verdict = desktop.is_running_instance(desktop.HOST, port)
    elapsed = time.monotonic() - t0
    assert verdict is True, "a Volksmond that was still starting was treated as a stranger"
    assert 0.9 < elapsed < 15.0, f"the probe took {elapsed:.1f}s; expected to wait out the start"


def test_a_dead_holder_is_not_waited_for():
    """Nothing listening at all (the holder died between our failed bind and the probe):
    decide immediately, do not burn the whole probe budget."""
    port = _free_port()
    t0 = time.monotonic()
    verdict = desktop.is_running_instance(desktop.HOST, port)
    elapsed = time.monotonic() - t0
    assert verdict is False
    assert elapsed < 5.0, f"a refused connection took {elapsed:.1f}s to give up"


def test_a_second_launch_hands_over_and_starts_no_server():
    """End to end: the port is held by a Volksmond, so main() raises the running copy and
    exits 0 without starting a second server."""
    with _holder(_http(VOLKSMOND_JSON)) as port:
        with _guard_env(port) as rec:
            code = desktop.main(["--server-only"])
    assert code == 0, f"a duplicate launch exited {code}, expected a clean 0"
    assert rec["served"] is None, "a duplicate launch started a second server"
    assert rec["fronted"] is not None, "the running instance was never raised"
    assert rec["fronted"][0] == f"http://{desktop.HOST}:{port}"


def test_browser_mode_hands_over_to_the_running_instance():
    """--browser must hand over too, and the handoff carries the mode so it can point the
    browser at the instance that IS running instead of raising a window it has not got."""
    with _holder(_http(VOLKSMOND_JSON)) as port:
        with _guard_env(port) as rec:
            code = desktop.main(["--browser"])
    assert code == 0
    assert rec["served"] is None, "--browser started a second server"
    assert rec["fronted"] == (f"http://{desktop.HOST}:{port}", "browser")


def test_a_foreign_service_on_the_port_does_not_brick_the_app():
    """An unrelated service on 8765 must not stop Volksmond starting: fall back to a
    spare port, and do not pretend the stranger is us."""
    with _holder(_http(b'{"detail":"Not Found"}', "404 Not Found")) as port:
        with _guard_env(port) as rec:
            code = desktop.main(["--server-only"])
            served = rec["served"]
            assert code == 0, f"a squatted port exited {code}"
            assert rec["fronted"] is None, "the app handed over to a stranger"
            assert served is not None, "the app did not start at all"
            spare = served.getsockname()[1]
    assert spare != port, "the app claims to have started on the port it could not bind"
    assert spare > 0


def test_a_free_port_starts_normally_on_the_fixed_port():
    """The common case: nothing is running, so the launch takes the fixed port itself."""
    port = _free_port()
    with _guard_env(port) as rec:
        code = desktop.main(["--server-only"])
        served = rec["served"]
        assert code == 0
        assert rec["fronted"] is None, "a first launch handed over to nothing"
        assert served is not None, "the first launch started no server"
        assert served.getsockname()[1] == port, "the first launch did not take the fixed port"


def test_the_claimed_socket_is_handed_straight_to_uvicorn():
    """The claim only holds if the socket itself is served. If start_server let uvicorn
    bind the port again, there would be a gap between our bind and its bind and the guard
    would be a check-then-act race after all."""
    seen = {}

    class _Config:
        def __init__(self, app, host=None, port=None, log_level=None):
            self.app, self.host, self.port = app, host, port

    class _Server:
        def __init__(self, config):
            self.config, self.should_exit = config, False

        def run(self, sockets=None):
            seen["sockets"] = sockets

    fake = types.ModuleType("uvicorn")
    fake.Config, fake.Server = _Config, _Server
    saved = sys.modules.get("uvicorn")
    sock = desktop.claim_port(0)
    assert sock is not None
    port = sock.getsockname()[1]
    try:
        sys.modules["uvicorn"] = fake
        server = desktop.start_server(sock)
        for _ in range(200):
            if "sockets" in seen:
                break
            time.sleep(0.01)
    finally:
        if saved is None:
            sys.modules.pop("uvicorn", None)
        else:
            sys.modules["uvicorn"] = saved
        sock.close()
    assert seen.get("sockets") == [sock], (
        f"uvicorn was not given the claimed socket ({seen.get('sockets')!r}); it would "
        "have bound the port itself, re-opening the race the claim closes")
    assert server.config.port == port


def test_the_probe_endpoint_still_exists_and_still_carries_the_marker():
    """The guard leans on an endpoint someone else owns. If /api/app-info is renamed or
    its name field changes, every second launch silently becomes a second instance
    again, so pin it here."""
    from live_transcribe.web import app as webapp
    info = webapp.app_info()
    assert info.get("name") == desktop.APP_MARKER, (
        f"{desktop.PROBE_PATH} no longer returns name={desktop.APP_MARKER!r}: the "
        "single-instance probe would classify a running Volksmond as a stranger")
    paths = {r.path for r in webapp.app.routes if hasattr(r, "path")}
    assert desktop.PROBE_PATH in paths, f"{desktop.PROBE_PATH} is gone from the app"


def test_the_mac_bundle_prohibits_multiple_instances():
    """LSMultipleInstancesProhibited stops LaunchServices starting the second copy before
    it ever runs Python. There is no standalone Info.plist: PyInstaller synthesises it
    from the info_plist dict in volksmond-mac.spec, so that is what we scan. (Behaviour
    itself needs a Mac; this only proves the key ships.)"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = open(os.path.join(root, "volksmond-mac.spec"), encoding="utf-8").read()
    assert '"LSMultipleInstancesProhibited": True' in spec, (
        "volksmond-mac.spec no longer sets LSMultipleInstancesProhibited; macOS will "
        "happily launch a second copy of the app")


def test_the_silent_random_port_fallback_is_gone():
    """The original defect, by name: a helper that answers "the port is taken, here is a
    different one" with no decision in between is what let two instances coexist."""
    assert not hasattr(desktop, "free_port"), (
        "desktop.free_port is back. Silently returning an OS-assigned port when 8765 is "
        "taken is exactly how a second instance used to start unnoticed; the port must be "
        "claimed (claim_port) and the fallback taken only after the holder is identified")


if __name__ == "__main__":
    test_the_claim_is_exclusive_and_self_healing()
    test_simultaneous_claims_have_exactly_one_winner()
    test_a_running_instance_is_recognised()
    test_a_foreign_service_is_not_mistaken_for_us()
    test_the_probe_waits_out_an_instance_that_is_still_starting()
    test_a_dead_holder_is_not_waited_for()
    test_a_second_launch_hands_over_and_starts_no_server()
    test_browser_mode_hands_over_to_the_running_instance()
    test_a_foreign_service_on_the_port_does_not_brick_the_app()
    test_a_free_port_starts_normally_on_the_fixed_port()
    test_the_claimed_socket_is_handed_straight_to_uvicorn()
    test_the_probe_endpoint_still_exists_and_still_carries_the_marker()
    test_the_mac_bundle_prohibits_multiple_instances()
    test_the_silent_random_port_fallback_is_gone()
    print("OK: the fixed port is claimed, not sampled; a second launch raises the running "
          "copy and exits; a stranger on the port never bricks the app.")
