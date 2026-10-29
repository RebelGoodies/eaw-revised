#pragma once
#include <atomic>
#include <chrono>
#include <filesystem>
#include <functional>
#include <optional>
#include <string_view>
#include <vector>

namespace eawr::mods_service {
// Stdout is the protocol stream. Stdin and stderr use /dev/null, as in the Windows helper job.
int run_posix_helper(const std::vector<std::filesystem::path>& args,
                     const std::atomic<bool>& cancelled,
                     const std::function<bool(std::string_view)>& consume,
                     std::optional<std::chrono::milliseconds> timeout = std::nullopt);
}
