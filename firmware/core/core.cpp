#include "core.hpp"
#include <algorithm>
#include <cstdlib>

namespace tuner {
namespace {
const char *boolean(bool x) { return x ? "true" : "false"; }
Text error_frame(std::string_view code) {
    Text value;
    value.add("{\"v\":1,\"op\":\"ERROR\",\"code\":\"").add(code).add("\"}");
    return value;
}
template <class Range> void append(Events &a, const Range &b) {
    for (const auto &item : b) a.push_back(item);
}
} // namespace

bool Profile::valid() const { return enabled && max_steps > 0 && max_rate > 0 && max_duration > 0 && budget > 0 && setup_us > 0 && pulse_us > 0 && hold_us > 0; }
Core::Core(Profile profile) : profile_(std::move(profile)) {}
Core::Entry *Core::find(int id) { for (auto &e : cache_) if (e.id == id) return &e; return nullptr; }
Text Core::reply(const Request &r, std::string_view op, std::string_view extra) const {
    Text s;
    s.add("{\"v\":1,\"op\":\"").add(op).add("\"");
    if (!r.minimal_stop) s.add(",\"session\":\"").add(r.session).add("\",\"id\":").number(r.id);
    s.add(extra).add("}");
    return s;
}
Events Core::end(bool aborted, std::string_view code) {
    enabled = false;
    if (!active_) return {};
    active_ = false;
    last_outcome_ = aborted ? "ABORTED" : "DONE";
    last_emitted_ = emitted_;
    Text extra;
    extra.add(",\"emitted_steps\":").number(emitted_).add(",\"requested_steps\":").number(move_.steps)
        .add(",\"enabled\":false,\"code\":\"").add(code).add("\"");
    auto response = reply(move_, last_outcome_, extra);
    if (auto *cached = find(move_.id)) cached->replies.push_back(response);
    return {response};
}
Events Core::latch(std::string_view code) {
    bool newly_latched = fault.empty();
    if (fault.empty()) fault = code;
    armed = edge_ = false;
    auto events = end(true, fault);
    if (newly_latched && events.empty())
        events.push_back(error_frame(fault));
    return events;
}
Events Core::inputs(bool deadman, bool stop, bool driver_fault, uint64_t now) {
    if (deadman && !deadman_ && !session_.empty()) edge_ = true;
    deadman_ = deadman; stop_ = stop; driver_fault_ = driver_fault;
    return tick(now);
}
Events Core::tick(uint64_t now) {
    if (now < now_) return latch("CLOCK_REVERSED");
    now_ = now;
    if (active_ && !pulse_stalled) {
        uint64_t bounded = std::min(now, deadline_);
        uint64_t elapsed = bounded > start_ + profile_.setup_us ? bounded - start_ - profile_.setup_us : 0;
        emitted_ = static_cast<int>(std::min<uint64_t>(std::abs(move_.steps), elapsed * move_.rate / 1000000));
    }
    if (stop_) return latch("STOP_INPUT");
    if (driver_fault_) return latch("DRIVER_FAULT");
    if (armed && !deadman_) return latch("DEADMAN_RELEASED");
    if (armed && now >= lease_) return latch("HEARTBEAT_TIMEOUT");
    if (active_) {
        if (now >= finish_ && !pulse_stalled) return end(false);
        if (now >= deadline_) return latch("MOVE_TIMEOUT");
    }
    return {};
}
Events Core::malformed(std::string_view code) {
    Events events = armed ? latch(code) : Events{};
    events.push_back(error_frame(code));
    return events;
}
Events Core::command(std::string_view line, uint64_t now) {
    Request r; Text error;
    if (!parse_request(line, r, error)) { auto events = tick(now); append(events, malformed(error)); return events; }
    Events events;
    if (r.op == "STOP") {
        now_ = std::max(now_, now);
        if (active_ && !pulse_stalled) {
            uint64_t bounded = std::min(now_, deadline_);
            uint64_t elapsed = bounded > start_ + profile_.setup_us ? bounded - start_ - profile_.setup_us : 0;
            emitted_ = static_cast<int>(std::min<uint64_t>(std::abs(move_.steps), elapsed * move_.rate / 1000000));
        }
        events = latch("STOP");
        if (r.minimal_stop) {
            events.push_back(reply(r,"ACK",",\"command\":\"STOP\""));
            return events;
        }
    } else events = tick(now);
    if (r.op == "HELLO" && r.session != session_) {
        if (armed || deadman_ || !fault.empty()) { events.push_back(reply(r,"ERROR",",\"code\":\"HELLO_BLOCKED\"")); return events; }
        session_ = r.session; high_id_ = budget_used = cache_next_ = 0; edge_ = false;
        for (auto &e : cache_) e = {};
        last_outcome_ = "NONE"; last_emitted_ = 0;
    }
    if (r.session != session_) { events.push_back(reply(r,"ERROR",",\"code\":\"SESSION_MISMATCH\"")); return events; }
    if (auto *cached = find(r.id)) {
        if (cached->payload != r.canonical) { append(events,latch("ID_REUSE")); events.push_back(reply(r,"ERROR",",\"code\":\"ID_REUSE\"")); }
        else append(events, cached->replies);
        return events;
    }
    if (r.id <= high_id_) { events.push_back(reply(r,"ERROR",",\"code\":\"STALE_ID\"")); return events; }
    high_id_ = r.id;
    auto &entry = cache_[cache_next_]; cache_next_ = (cache_next_ + 1) % 32;
    entry = {r.id, r.canonical, {}};
    auto &replies = entry.replies;
    error.clear();
    if (r.op == "HELLO") {
        Text extra;
        extra.add(",\"device\":\"pianotuner-v1\",\"profile_hash\":\"").add(profile_.hash)
            .add("\",\"firmware_build\":\"").add(profile_.enabled ? "pianotuner-v1-simulation" : "pianotuner-v1-disabled")
            .add("\",\"actuation_enabled\":").add(boolean(profile_.enabled))
            .add(",\"max_move_steps\":").number(profile_.max_steps)
            .add(",\"max_rate_hz\":").number(profile_.max_rate)
            .add(",\"max_duration_ms\":").number(profile_.max_duration)
            .add(",\"absolute_step_budget\":").number(profile_.budget);
        replies.push_back(reply(r, "HELLO", extra));
    } else if (r.op == "STATUS") {
        Text extra;
        extra.add(",\"armed\":").add(boolean(armed)).add(",\"enabled\":").add(boolean(enabled))
            .add(",\"fault\":\"").add(fault).add("\",\"deadman\":").add(boolean(deadman_))
            .add(",\"stop_input\":").add(boolean(stop_)).add(",\"driver_fault\":").add(boolean(driver_fault_))
            .add(",\"budget_used\":").number(budget_used).add(",\"busy\":").add(boolean(active_))
            .add(",\"last_outcome\":\"").add(last_outcome_).add("\",\"emitted_steps\":").number(last_emitted_);
        replies.push_back(reply(r, "STATUS", extra));
    } else if (r.op == "CLEAR_FAULT") {
        if (deadman_ || stop_ || driver_fault_) error = "CAUSE_PRESENT";
        else { append(events,end(true,"CLEAR_FAULT")); fault.clear(); armed = edge_ = false; }
    } else if (r.op == "DISARM") {
        append(events,end(true,"DISARM")); armed = edge_ = false;
    } else if (r.op == "ARM") {
        if (!fault.empty()) error = "FAULT_LATCHED";
        else if (!profile_.valid()) error = "UNCOMMISSIONED";
        else if (r.profile_hash != profile_.hash) error = "PROFILE_MISMATCH";
        else if (armed || !deadman_ || !edge_ || !r.confirmed) error = "ARM_INTERLOCK";
        else { armed = true; edge_ = false; lease_ = now_ + 500000; }
    } else if (r.op == "HEARTBEAT") {
        if (armed) lease_ = now_ + 500000;
    } else if (r.op == "MOVE") {
        int n = std::abs(r.steps);
        uint64_t duration = profile_.setup_us + (static_cast<uint64_t>(n) * 1000000 + r.rate - 1) / r.rate + profile_.pulse_us + profile_.hold_us;
        if (!fault.empty()) error = "FAULT_LATCHED";
        else if (!armed) error = "NOT_ARMED";
        else if (active_) error = "BUSY";
        else if (n > profile_.max_steps || r.rate > profile_.max_rate || r.duration > profile_.max_duration) error = "LIMIT_EXCEEDED";
        else if (duration >= static_cast<uint64_t>(r.duration) * 1000 || static_cast<uint64_t>(2) * profile_.pulse_us * r.rate >= 1000000) error = "DURATION_INVALID";
        else if (static_cast<int64_t>(budget_used) + n > profile_.budget) error = "BUDGET_EXHAUSTED";
        else { budget_used += n; enabled = active_ = true; move_ = r; emitted_ = 0; start_ = now_; finish_ = now_ + duration; deadline_ = now_ + static_cast<uint64_t>(r.duration) * 1000; }
    }
    if (!error.empty()) {
        Text extra; extra.add(",\"code\":\"").add(error).add("\"");
        replies.push_back(reply(r, "ERROR", extra));
    } else if (replies.empty() && r.op != "HEARTBEAT") {
        Text extra; extra.add(",\"command\":\"").add(r.op).add("\"");
        replies.push_back(reply(r, "ACK", extra));
    }
    append(events,replies);
    return events;
}
void Framer::feed(Core &core, const char *data, size_t length, uint64_t now, Output output, void *context) {
    auto collect = [&](const Events &next) {
        for (const auto &event : next) output(event, context);
    };
    for (size_t i = 0; i < length; ++i) {
        char c = data[i];
        if (discard_) { if (c == '\n') discard_ = false; continue; }
        bytes_[used_++] = c;
        if (used_ == 512 && c != '\n') {
            used_ = 0; discard_ = true;
            collect(core.malformed("FRAME_TOO_LONG"));
        } else if (c == '\n') {
            auto next = core.command(std::string_view(bytes_.data(),used_), now);
            used_ = 0;
            collect(next);
        }
    }
    collect(core.tick(now));
}
} // namespace tuner
