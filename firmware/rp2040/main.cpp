// Deliberately non-energizing R1 build: no actuation GPIO is configured or written.
// There is no pin map until components, polarity and stop circuit are reviewed.
#include "core.hpp"
#include "pico/stdlib.h"
#include "hardware/watchdog.h"

static tuner::Core core; // zero caps, disabled; host commands cannot change them
static tuner::Framer framer;

static void emit(const tuner::Events &events) {
    for (const auto &e : events) puts(e.c_str());
}
static void emit_one(const tuner::Text &event, void *) { puts(event.c_str()); }

int main() {
    static_assert(PICO_DEFAULT_UART == 0, "standard Pico board expected");
    stdio_init_all(); // USB CDC only, UART disabled by CMake
    watchdog_enable(250, true);
    while (true) {
        auto now = time_us_64();
        // Bounded traffic work: local safety checks run between every byte.
        int ch = getchar_timeout_us(0);
        if (ch != PICO_ERROR_TIMEOUT) {
            char c = static_cast<char>(ch);
            framer.feed(core, &c, 1, now, emit_one, nullptr);
        } else emit(core.tick(now));
        watchdog_update();
        tight_loop_contents();
    }
}
