#include "linux_platform.hpp"
#include "launcher_state.hpp"

#include <cstdlib>
#include <iostream>

namespace {
using namespace eawr::launcher;
std::optional<fs::path> environment_path(const char* name) {
    const char* value = std::getenv(name);
    return value && *value ? std::optional<fs::path>(path_from_utf8(value)) : std::nullopt;
}
}

int main(int argc, char** argv) {
    using namespace eawr::launcher;
    bool headless = false;
    try {
        std::vector<std::wstring> args;
        for (int i = 1; i < argc; ++i) args.push_back(path_to_wide(fs::path(argv[i])));
        Options options = parse_command_line(args);
#ifdef EAWR_MODS_ENTRY_POINT
        options.mods = true;
#endif
        headless = options.no_dialogs;
        options.launcher_dir = fs::read_symlink("/proc/self/exe").parent_path();
        if (options.user_data.empty()) options.user_data = default_user_data_dir();
        if (!options.user_data.empty()) options.user_data = fs::absolute(options.user_data).lexically_normal();
        if (options.project) options.project = fs::absolute(*options.project).lexically_normal();
        if (!options.game_root) options.game_root = environment_path("EAWR_EAW_GAME_ROOT");
        if (!options.godot) options.godot = environment_path("EAWR_GODOT_EXECUTABLE");
        if (options.help) {
            std::cout << "eawr-launcher [options]\n"
                "  --game-root <folder>    Forces of Corruption folder containing GameData and corruption\n"
                "  --choose-game           choose another installation (also: hold Shift while starting)\n"
                "  --godot <file>          override bundled Godot 4.7.2\n"
                "  --project <folder>      override bundled project\n"
                "  --user-data <folder>    override launcher state/log location\n"
                "  --render-thread MODE    safe or separate (overrides saved setting)\n"
                "  --safe-render-thread    alias for safe mode\n"
                "  --mods                  open optional EAWR Mods\n"
                "  --allow-unknown-build   allow an unverified game build\n"
                "  --no-dialogs            log errors without opening windows\n"
                "  --godot-quit-after <n>  close after n frames\n"
                "  -- <args>               forward arguments to the game\n";
            return kExitOk;
        }
        auto ui = make_linux_ui(headless);
        if (linux_shift_pressed(headless)) options.choose_game = true;
        auto registry = make_linux_registry();
        auto runner = make_linux_process_runner();
        Log log(options.user_data.empty() ? fs::path{} : options.user_data / "launcher" / "launcher.log");
        return run_launcher(options, *registry, *ui, *runner, log);
    } catch (const std::exception& error) {
        auto ui = make_linux_ui(headless);
        ui->show_error("Empire at War Revised", std::string("The launcher could not start: ") + error.what());
        return kExitError;
    }
}
