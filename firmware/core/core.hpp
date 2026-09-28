#pragma once
#include <array>
#include <cstdint>
#include "fixed.hpp"

namespace tuner {
struct Profile {
    Text hash = "UNCOMMISSIONED";
    bool enabled = false;
    int max_steps = 0, max_rate = 0, max_duration = 0, budget = 0;
    int setup_us = 0, pulse_us = 0, hold_us = 0;
    static Profile simulation() { return {"simulation-v1", true, 8, 100, 250, 256, 10, 10, 10}; }
    bool valid() const;
};
struct Request {
    Text op, session, profile_hash, canonical;
    int id = 0, steps = 0, rate = 0, duration = 0;
    bool confirmed = false, minimal_stop = false;
};
bool parse_request(std::string_view frame, Request &out, Text &error);

// All inputs and time come from the adapter, never from host-provided caps.
class Core {
public:
    explicit Core(Profile profile = {});
    Events command(std::string_view line, uint64_t now_us);
    Events tick(uint64_t now_us);
    Events inputs(bool deadman, bool stop, bool driver_fault, uint64_t now_us);
    Events malformed(std::string_view code);
    bool armed = false, enabled = false;
    bool pulse_stalled = false; // native fault injection; never a wire command
    Text fault;
    int budget_used = 0;
private:
    struct Entry { int id = 0; Text payload; CachedReplies replies; };
    Profile profile_;
    Text session_, last_outcome_ = "NONE";
    int high_id_ = 0, last_emitted_ = 0, cache_next_ = 0;
    bool deadman_ = false, stop_ = false, driver_fault_ = false, edge_ = false, active_ = false;
    uint64_t now_ = 0, lease_ = 0, start_ = 0, finish_ = 0, deadline_ = 0;
    int emitted_ = 0;
    Request move_;
    std::array<Entry, 32> cache_{};
    Entry *find(int id);
    Events end(bool aborted, std::string_view code = "");
    Events latch(std::string_view code);
    Text reply(const Request &, std::string_view op, std::string_view extra = "") const;
};

// Fixed wire storage; oversize input is discarded through its newline.
class Framer {
public:
    using Output = void (*)(const Text &, void *);
    void feed(Core &core, const char *bytes, size_t length, uint64_t now_us, Output output, void *context);
private:
    std::array<char, 512> bytes_{};
    size_t used_ = 0;
    bool discard_ = false;
};
} // namespace tuner
