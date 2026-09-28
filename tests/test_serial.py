import json

import pytest

from pianotuner.adapters.serial import SerialActuator
from pianotuner.simulation.firmware import FirmwareModel, FirmwareProfile


class FakeSerial:
    def __init__(self):
        self.model = FirmwareModel(FirmwareProfile.simulation())
        self.now = 0
        self.incoming = bytearray()
        self.writes = []
        self.is_open = True
        self.drop = set()
        self.chunk = 4096
        self.partial = False

    def write(self,data):
        self.writes.append(data)
        if self.partial:
            return len(data)//2
        self.queue(self.model.feed(data,self.now))
        return len(data)

    def queue(self,events):
        for event in events:
            if event["op"] not in self.drop:
                self.incoming.extend((json.dumps(event)+"\n").encode())

    def read(self,n):
        n=min(n,self.chunk)
        data=bytes(self.incoming[:n])
        del self.incoming[:n]
        return data

    def close(self):
        self.is_open=False


def ready():
    fake=FakeSerial()
    port=SerialActuator(fake,"simulation-v1", "0"*32)
    port.handshake(0)
    port.poll(0)
    fake.model.set_inputs(True,now=0)
    port.arm(0)
    port.poll(0)
    return port,fake


def test_handshake_does_not_arm_without_local_edge():
    fake=FakeSerial()
    port=SerialActuator(fake,"simulation-v1")
    with pytest.raises(RuntimeError):
        port.arm(0)
    port.handshake(0)
    assert port.poll(0)[0]["op"]=="HELLO"
    assert not fake.model.armed
    port.arm(0)
    assert port.poll(0)[0]["code"]=="ARM_INTERLOCK"


def test_fragmented_and_coalesced_responses():
    port,fake=ready()
    ident=port.move(8,100,250,0)
    fake.chunk=7
    events=[]
    for _ in range(30):
        events+=port.poll(.01)
    assert any(e.get("id")==ident and e["op"]=="ACK" for e in events)
    fake.now=.1
    fake.queue(fake.model.tick(.1))
    fake.chunk=4096
    port.disarm(.1)
    result=port.poll(.1)
    assert {x["op"] for x in result}=={"DONE","ACK"}
    assert port.disabled_confirmed


@pytest.mark.parametrize("drop,when,code",[("ACK",.251,"ACK_TIMEOUT"),("DONE",.351,"MOVE_RESULT_TIMEOUT")])
def test_lost_motion_reply_no_retry(drop,when,code):
    port,fake=ready()
    fake.drop.add(drop)
    port.move(8,100,250,0)
    port.poll(.01)
    if drop=="DONE":
        fake.queue(fake.model.tick(.1))
    fake.now=when
    result=port.poll(when)
    assert any(e.get("code")==code and e["outcome"]=="UNKNOWN" for e in result)
    commands=[json.loads(w) for w in fake.writes]
    assert len([c for c in commands if c["op"]=="MOVE"])==1
    assert any(c["op"]=="STOP" for c in commands)
    assert any(c["op"]=="STATUS" for c in commands)
    assert fake.model.budget_used==8
    with pytest.raises(ConnectionError):
        port.move(1,100,250,when)


def test_partial_write_never_retry_and_retains_outstanding_exposure():
    port,fake=ready()
    fake.partial=True
    with pytest.raises(ConnectionError):
        port.move(8,100,250,0)
    assert any(w["requested_steps"]==8 for w in port.waiting.values())
    assert len([w for w in fake.writes if json.loads(w)["op"]=="MOVE"])==1


def test_mismatched_profile_disables_handshake():
    fake=FakeSerial()
    port=SerialActuator(fake,"wrong")
    port.handshake(0)
    assert any(e.get("code")=="CAPABILITY_MISMATCH" for e in port.poll(0))
    assert not port.handshake_ok and port.failed


def test_unknown_and_old_session_done_cannot_settle_move():
    port,fake=ready()
    ident=port.move(8,100,250,0)
    port.poll(0)
    fake.queue([dict(v=1,session="1"*32,id=ident,op="DONE",emitted_steps=8),
                dict(v=1,session=port.session_id,id=ident+999,op="DONE",emitted_steps=8)])
    assert port.poll(.1)==[]
    assert ident in port.waiting


@pytest.mark.parametrize("change",[{"emitted_steps":True},{"emitted_steps":7},{"requested_steps":-8},{"enabled":True}])
def test_invalid_completion_is_transport_fault(change):
    port,fake=ready()
    ident=port.move(8,100,250,0)
    port.poll(0)
    completion=dict(v=1,session=port.session_id,id=ident,op="DONE",emitted_steps=8,
                    requested_steps=8,enabled=False)
    fake.queue([completion|change])
    assert any(e.get("code")=="MALFORMED_RESPONSE" for e in port.poll(.1))
    assert port.failed and ident in port.waiting


def test_disconnect_fault_and_hardware_template_cannot_open():
    port,fake=ready()
    fake.is_open=False
    assert any(e.get("code")=="DISCONNECTED" for e in port.poll(0))
    with pytest.raises(ValueError):
        SerialActuator.open("DO-NOT-OPEN", "configs/hardware.UNCOMMISSIONED.json")
