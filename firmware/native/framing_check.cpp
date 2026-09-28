#include "core.hpp"
#include <cstdio>
#include <cstring>

struct Capture {
    size_t count = 0;
    tuner::Text last;
};
static void record(const tuner::Text &event, void *context) {
    auto &capture = *static_cast<Capture *>(context);
    ++capture.count;
    capture.last = event;
}
int main() {
    tuner::Core core;
    tuner::Framer framer;
    Capture capture;
    const char *hello = "{\"v\":1,\"session\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"id\":1,\"op\":\"HELLO\"}\n";
    for (size_t i = 0; i < std::strlen(hello); ++i) framer.feed(core, hello+i, 1, 0, record, &capture);
    if (capture.count != 1) return 1;
    char batch[1500]{};
    size_t length = 0;
    for (int id = 2; id < 12; ++id)
        length += std::snprintf(batch + length, sizeof(batch) - length,
            "{\"v\":1,\"session\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"id\":%d,\"op\":\"STATUS\"}\n", id);
    framer.feed(core, batch, length, 0, record, &capture);
    if (capture.count != 11) return 2;
    char overlong[530]; std::memset(overlong, 'x', sizeof(overlong)); overlong[529] = '\n';
    framer.feed(core, overlong, sizeof(overlong), 0, record, &capture);
    if (capture.count != 12 || std::string_view(capture.last).find("FRAME_TOO_LONG") == std::string_view::npos) return 3;
    std::puts("fragmented and coalesced framing passed with bounded streaming output");
    return 0;
}
