import json
from pathlib import Path

import pytest

from pianotuner.adapters.loopback import LoopbackActuator
from pianotuner.protocol import FrameDecoder, ProtocolError, encode_request, parse_request
from pianotuner.simulation.firmware import FirmwareModel, FirmwareProfile

SESSION = "0" * 32


def request(ident, op, **fields):
    return dict(v=1, session=SESSION, id=ident, op=op, **fields)


def armed():
    model = FirmwareModel(FirmwareProfile.simulation())
    model.command(request(1, "HELLO"), 0)
    model.set_inputs(True, now=0)
    assert model.command(request(2, "ARM", profile_hash="simulation-v1", operator_confirmed=True), 0)[0]["op"] == "ACK"
    assert model.armed and not model.enabled
    return model


def test_minimal_stop_without_session():
    model = armed()
    assert model.command({"v": 1, "op": "STOP"}, 0)[-1]["op"] == "ACK"
    assert not model.armed and not model.enabled and model.fault == "STOP"
    assert parse_request(b'{"v":1,"op":"STOP"}\n') == {"v":1,"op":"STOP"}


def test_complete_stop_frame_precedes_due_normal_completion():
    model = armed()
    model.command(request(3,"MOVE",steps=8,rate_hz=100,max_duration_ms=250),0)
    events=model.feed(b'{"v":1,"op":"STOP"}\n',.1)
    assert events[0]["op"]=="ABORTED"
    assert events[0]["emitted_steps"]==8
    assert all(event["op"]!="DONE" for event in events)


def test_ordinary_stop_consumes_id_and_checks_session():
    model=armed()
    stop=request(3,"STOP")
    assert model.command(stop,0)[-1]["op"]=="ACK"
    assert model.high_id==3
    assert model.command(stop,0)[0]["op"]=="ACK"
    assert model.command(request(3,"STATUS"),0)[-1]["code"]=="ID_REUSE"


def test_interlock_fault_while_waiting_strike_is_reported_once():
    model=armed()
    assert model.set_inputs(False,now=.1)==[{"v":1,"op":"ERROR","code":"DEADMAN_RELEASED"}]
    assert model.tick(.2)==[]
    assert not model.armed and not model.enabled


def test_bounded_fragmentation_discard_and_recovery():
    decoder = FrameDecoder()
    frame = encode_request(request(1, "HELLO"))
    assert decoder.feed(frame[:7]) == []
    assert decoder.feed(frame[7:] + frame) == [frame, frame]
    error = decoder.feed(b"x" * 10_000)
    assert len(error) == 1 and isinstance(error[0], ProtocolError)
    assert not decoder.buffer and decoder.discarding
    assert decoder.feed(b"discard this\n" + frame) == [frame]


def test_oversize_while_armed_faults_before_newline():
    model = armed()
    result = model.feed(b" " * 512, 0)
    assert result[-1]["code"] == "FRAME_TOO_LONG"
    assert not model.armed
    assert model.feed(b"junk\n" + encode_request(request(3,"STATUS")),0)[-1]["fault"] == "FRAME_TOO_LONG"


def test_completion_and_duplicate_do_not_reexecute():
    model = armed()
    move = request(3,"MOVE",steps=8,rate_hz=100,max_duration_ms=100)
    assert model.command(move,0)[0]["op"] == "ACK"
    assert model.tick(.12)[0]["emitted_steps"] == 8
    replies = model.command(dict(reversed(list(move.items()))), .12)
    assert [x["op"] for x in replies] == ["ACK","DONE"]
    assert model.budget_used == 8


def test_consumed_error_is_cached_and_clear_never_rearms():
    model = armed()
    model.command(request(3,"MOVE",steps=9,rate_hz=100,max_duration_ms=250),0)
    assert model.command(request(3,"STATUS"),0)[-1]["code"] == "ID_REUSE"
    model.set_inputs(False,now=0)
    model.command(request(4,"CLEAR_FAULT"),0)
    assert not model.armed and not model.fault
    assert model.command(request(5,"ARM",profile_hash="simulation-v1",operator_confirmed=True),0)[0]["code"] == "ARM_INTERLOCK"


def test_simulated_link_loss_leaves_local_lease_enforced():
    port = LoopbackActuator()
    port.arm(0)
    port.move(8,100,250,0)
    port.connected = False
    with pytest.raises(ConnectionError):
        port.poll(.1)
    port.model.tick(.5)
    assert not port.model.armed and not port.model.enabled
    assert port.model.budget_used == 8


TRACES = json.loads((Path(__file__).parent / "conformance/protocol_traces.json").read_text())


@pytest.mark.parametrize("trace",TRACES,ids=lambda x:x["name"])
def test_python_golden_trace(trace):
    from tools.check_firmware_conformance import python_trace
    flat = [e for row in python_trace(trace["lines"],trace["simulation"]) for e in row]
    for expectation in trace["expect"]:
        assert any(all(event.get(k)==v for k,v in expectation.items()) for event in flat)
