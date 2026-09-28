#include "core.hpp"
#include <iostream>
#include <sstream>

int main(int argc, char **argv) {
    bool simulated = argc > 1 && std::string(argv[1]) == "--simulation";
    tuner::Core core(simulated ? tuner::Profile::simulation() : tuner::Profile{});
    uint64_t now = 0;
    std::string line;
    while (std::getline(std::cin,line)) {
        tuner::Events events;
        if (line.rfind("@time ",0) == 0) {
            now = std::stoull(line.substr(6)); events = core.tick(now);
        } else if (line.rfind("@inputs ",0) == 0) {
            int d = 0, s = 0, f = 0; std::istringstream in(line.substr(8)); in >> d >> s >> f;
            events = core.inputs(d != 0,s != 0,f != 0,now);
        } else if (line == "@reset") {
            core = tuner::Core(simulated ? tuner::Profile::simulation() : tuner::Profile{});
        } else if (line.rfind("@stall ",0) == 0) {
            core.pulse_stalled = line.substr(7) == "1";
        } else {
            events = core.command(line + "\n",now);
        }
        std::cout << '[';
        for (size_t i = 0; i < events.size(); ++i) { if (i) std::cout << ','; std::cout << events[i].c_str(); }
        std::cout << "]\n";
    }
}
