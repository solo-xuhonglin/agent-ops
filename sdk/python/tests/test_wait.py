import pytest
from zszc.agentops import RunCancelled, RunFailed, Timeout, create_client
from zszc.agentops.client import Run

SESSION_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "44444444-4444-4444-4444-444444444444"


def run_view(**over):
    view = {
        "run_id": RUN_ID,
        "session_id": SESSION_ID,
        "status": "running",
        "result": None,
        "error": None,
        "cancel_requested": False,
    }
    view.update(over)
    return view


@pytest.fixture
def run(fake):
    with create_client("k", base_url=fake.base_url, poll_interval=0.05) as client:
        yield Run(client, RUN_ID, SESSION_ID)


def serving(fake, views):
    """Serve the run endpoint with a per-request sequence; the last view repeats."""
    remaining = iter(views)

    def handler(request):
        return 200, next(remaining, views[-1])

    fake.route("GET", f"/v1/runs/{RUN_ID}", handler=handler)


def test_wait_polls_until_success(fake, run):
    serving(fake, [run_view(status="running"), run_view(status="succeeded", result="stub: hi")])

    result = run.wait()

    assert result.output == "stub: hi"
    assert run.status == "succeeded"


def test_wait_on_terminal_run_resolves_with_one_request(fake, run):
    serving(fake, [run_view(status="succeeded", result="done")])

    run.wait()

    assert len(fake.requests) == 1


def test_wait_raises_run_failed(fake, run):
    serving(fake, [run_view(status="failed", error="boom")])

    with pytest.raises(RunFailed) as exc:
        run.wait()

    assert exc.value.error == "boom"


def test_wait_raises_run_cancelled(fake, run):
    serving(fake, [run_view(status="cancelled")])

    with pytest.raises(RunCancelled):
        run.wait()


def test_wait_timeout_leaves_the_run_running(fake, run):
    serving(fake, [run_view(status="running")])

    with pytest.raises(Timeout):
        run.wait(timeout=0.1)

    assert run.status == "running"
