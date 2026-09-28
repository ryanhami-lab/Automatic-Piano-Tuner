"""Create the human-reviewable shared protocol trace fixture, no randomness."""
import json
from pathlib import Path

SESSION = "0123456789abcdef0123456789abcdef"
OTHER = "abcdef0123456789abcdef0123456789"


def cmd(ident, op, **fields):
    return json.dumps(dict(v=1, session=SESSION, id=ident, op=op, **fields), separators=(",", ":"))


def setup():
    return [cmd(1, "HELLO"), "@inputs 1 0 0",
            cmd(2, "ARM", profile_hash="simulation-v1", operator_confirmed=True)]


def move(ident=3, **changes):
    return cmd(ident, "MOVE", **(dict(steps=8, rate_hz=100, max_duration_ms=250) | changes))


traces = []


def add(name, lines, expect=(), simulation=True):
    traces.append(dict(name=name, simulation=simulation, lines=lines, expect=list(expect)))


add("disabled_startup", setup() + [move(), cmd(4, "STATUS")],
    [{"op":"ERROR","code":"UNCOMMISSIONED"}, {"op":"STATUS","armed":False,"budget_used":0}], False)
add("finite_move_duplicate_and_canonical_order", setup() + [move(), move(), "@time 100000", move(),
    json.dumps(json.loads(move()), sort_keys=True, indent=None), cmd(4,"STATUS")],
    [{"op":"DONE","emitted_steps":8},{"op":"STATUS","budget_used":8,"enabled":False}])
add("busy_and_id_reuse_fault", setup() + [move(), move(4), move(3, steps=-8), cmd(5,"STATUS")],
    [{"op":"ERROR","code":"BUSY"},{"op":"ABORTED","code":"ID_REUSE"},{"op":"STATUS","budget_used":8}])
add("heartbeat_duplicate_does_not_extend", setup() + ["@time 300000",cmd(3,"HEARTBEAT"),"@time 700000",cmd(3,"HEARTBEAT"),"@time 800000",cmd(4,"STATUS")],
    [{"op":"STATUS","fault":"HEARTBEAT_TIMEOUT","armed":False}])
add("no_heartbeat_lease_from_arm", setup()+["@time 500000",cmd(3,"STATUS")], [{"op":"STATUS","fault":"HEARTBEAT_TIMEOUT"}])
add("deadman_release_before_completion", setup()+[move(),"@time 40000","@inputs 0 0 0",cmd(4,"STATUS")], [{"op":"ABORTED","code":"DEADMAN_RELEASED","emitted_steps":3}])
add("stop_at_completion", setup()+[move(),'{"v":1,"op":"STOP"}',"@time 100000",cmd(4,"STATUS")], [{"op":"ABORTED","code":"STOP"},{"op":"STATUS","fault":"STOP"}])
add("physical_interlock_and_recovery", setup()+[move(),"@inputs 1 1 0",cmd(4,"CLEAR_FAULT"),"@inputs 0 0 0",cmd(5,"CLEAR_FAULT"),cmd(6,"STATUS")], [{"op":"ERROR","code":"CAUSE_PRESENT"},{"op":"STATUS","armed":False,"fault":"","budget_used":8}])
add("driver_fault", setup()+[move(),"@inputs 1 0 1",cmd(4,"STATUS")], [{"op":"ABORTED","code":"DRIVER_FAULT"}])
add("stalled_finite_engine_deadline", setup()+[move(),"@stall 1","@time 250000",cmd(4,"STATUS")], [{"op":"ABORTED","code":"MOVE_TIMEOUT"},{"op":"STATUS","budget_used":8}])
add("disarm_preserves_budget", setup()+[move(),cmd(4,"DISARM"),cmd(5,"STATUS")], [{"op":"ABORTED","code":"DISARM"},{"op":"STATUS","armed":False,"budget_used":8}])
add("reset_disables",setup()+[move(),"@reset",cmd(4,"STATUS"),cmd(5,"HELLO"),cmd(6,"STATUS")], [{"op":"ERROR","code":"SESSION_MISMATCH"},{"op":"STATUS","armed":False,"budget_used":0}])
add("limits",setup()+[move(3,steps=9),move(4,rate_hz=101),move(5,max_duration_ms=251),move(6,max_duration_ms=80),cmd(7,"STATUS")], [{"op":"ERROR","code":"LIMIT_EXCEEDED"},{"op":"ERROR","code":"DURATION_INVALID"},{"op":"STATUS","budget_used":0}])
add("session_mismatch",setup()+[move().replace(SESSION,OTHER),cmd(4,"STATUS")], [{"op":"ERROR","code":"SESSION_MISMATCH"},{"op":"STATUS","budget_used":0}])
add("new_hello_cannot_clear_fault",setup()+['{"v":1,"op":"STOP"}',"@inputs 0 0 0",cmd(4,"HELLO").replace(SESSION,OTHER),cmd(5,"STATUS")], [{"op":"ERROR","code":"HELLO_BLOCKED"},{"op":"STATUS","fault":"STOP"}])
add("edge_required_after_hello",["@inputs 1 0 0",cmd(1,"HELLO"),"@inputs 0 0 0",cmd(2,"HELLO"),cmd(3,"ARM",profile_hash="simulation-v1",operator_confirmed=True)], [{"op":"ERROR","code":"HELLO_BLOCKED"},{"op":"ERROR","code":"ARM_INTERLOCK"}])
add("evicted_old_id_rejected",setup()+[cmd(i,"STATUS") for i in range(3,38)]+[cmd(1,"HELLO")], [{"op":"ERROR","code":"STALE_ID"}])
budget_lines=setup()
ident=3
for i in range(32):
    budget_lines += [move(ident,steps=8 if i%2 else -8),f"@time {(i+1)*100000}",cmd(ident+1,"HEARTBEAT")]
    ident+=2
budget_lines += [move(ident),cmd(ident+1,"STATUS")]
add("absolute_budget_not_net",budget_lines,[{"op":"ERROR","code":"BUDGET_EXHAUSTED"},{"op":"STATUS","budget_used":256}])
bad = [
    '{"v":1,"v":1,"op":"STOP"}',
    move().replace('"steps":8','"steps":true'),
    move().replace('"steps":8','"steps":2147483648'),
    move().replace('"steps":8','"steps":-2147483648'),
    move().replace('"steps":8','"steps":01'),
    move().replace('"steps":8','"steps":+1'),
    move().replace('"steps":8','"steps":1.0'),
    move().replace('"steps":8','"steps":1e0'),
    move().replace('"steps":8','"steps":NaN'),
    move().replace('"steps":8','"steps":null'),
    move().replace('"steps":8','"steps":{}'),
    move().replace('"steps":8','"steps":[1]'),
    move().replace('"rate_hz":100','"rate_hz":0'),
    move().replace('"rate_hz":100','"rate_hz":-1'),
    move().replace('"steps":8','"steps":0'),
    move().replace('"MOVE"','"M\\u004fVE"'),
    move().replace('"MOVE"','"MÖVE"'),
    move().replace('"v":1','"v":2'),
    move().replace('"v":1,','"v":1 '),
    move().replace('"v":1,','"v" 1,'),
    move()[:-1]+',}',
    move()+'{}',
    move()[:-1]+',"extra":1}',
    '{"op":"STOP"}',
    '{"v":true,"op":"STOP"}',
    ' '*512,
]
for index, malformed in enumerate(bad):
    code="FRAME_TOO_LONG" if index==len(bad)-1 else "MALFORMED"
    add(f"strict_wire_{index:02d}",setup()+[malformed,cmd(4,"STATUS")], [{"op":"ERROR","code":code},{"op":"STATUS","armed":False,"fault":code}])

if __name__ == "__main__":
    path=Path(__file__).resolve().parents[1]/"tests/conformance/protocol_traces.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(traces,indent=2)+"\n",encoding="utf-8")
    print(f"Wrote {len(traces)} shared golden traces")
