#pragma once
#include "launcher_flow.hpp"
#include <memory>

namespace eawr::launcher {
std::unique_ptr<RegistryReader> make_linux_registry();
std::unique_ptr<ProcessRunner> make_linux_process_runner();
std::unique_ptr<Ui> make_linux_ui(bool headless);
// Sample after make_linux_ui initializes GTK. Unavailable keyboard state means false.
bool linux_shift_pressed(bool headless);
}
