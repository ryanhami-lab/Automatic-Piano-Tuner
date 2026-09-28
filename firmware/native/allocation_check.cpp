#include "core.hpp"
#include <cstdio>
#include <cstdlib>
#include <new>

static bool tracking = false;
static size_t allocations = 0;
void *operator new(size_t size) {
    if (tracking) ++allocations;
    if (void *p = std::malloc(size)) return p;
    throw std::bad_alloc();
}
void *operator new[](size_t size) { return ::operator new(size); }
void operator delete(void *p) noexcept { std::free(p); }
void operator delete[](void *p) noexcept { std::free(p); }
void operator delete(void *p, size_t) noexcept { std::free(p); }
void operator delete[](void *p, size_t) noexcept { std::free(p); }

int main() {
    static_assert(sizeof(tuner::Core) < 110000, "core must leave room for SDK and stack in RP2040 RAM");
    tracking = true;
    tuner::Core core(tuner::Profile::simulation());
    auto hello = core.command("{\"v\":1,\"session\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"id\":1,\"op\":\"HELLO\"}\n", 0);
    core.inputs(true, false, false, 0);
    auto arm = core.command("{\"v\":1,\"session\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"id\":2,\"op\":\"ARM\",\"profile_hash\":\"simulation-v1\",\"operator_confirmed\":true}\n", 0);
    auto move = core.command("{\"v\":1,\"session\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"id\":3,\"op\":\"MOVE\",\"steps\":8,\"rate_hz\":100,\"max_duration_ms\":200}\n", 0);
    auto done = core.tick(100000);
    auto duplicate = core.command("{\"op\":\"MOVE\",\"v\":1,\"session\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"id\":3,\"steps\":8,\"rate_hz\":100,\"max_duration_ms\":200}\n", 100000);
    tuner::Framer framer;
    constexpr char stop[] = "{\"v\":1,\"op\":\"STOP\"}\n";
    size_t stopped = 0;
    framer.feed(core, stop, sizeof(stop)-1, 100000,
        [](const tuner::Text &, void *count) { ++*static_cast<size_t *>(count); }, &stopped);
    tracking = false;
    std::printf("core_bytes=%zu event_buffer_bytes=%zu heap_allocations=%zu\n", sizeof(core), sizeof(tuner::Events), allocations);
    return allocations == 0 && hello.size() == 1 && arm.size() == 1 && move.size() == 1 &&
           done.size() == 1 && duplicate.size() == 2 && stopped > 0 && !core.enabled ? 0 : 1;
}
