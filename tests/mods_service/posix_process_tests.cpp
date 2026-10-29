#include "posix_process.hpp"
#include <chrono>
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <iostream>
#include <stdexcept>
#include <string>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>

using namespace std::chrono_literals;
namespace fs = std::filesystem;
using eawr::mods_service::run_posix_helper;

namespace {
void require(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
int helper(std::string_view mode, int argc, char** argv) {
    if (mode == "echo") {
        require(argc == 4, "missing UTF-8 argument");
        require(::fcntl(std::stoi(argv[2]), F_GETFD) == -1 && errno == EBADF, "inherited unrelated fd");
        std::cerr << std::string(256 * 1024, 'x');
        std::cout << argv[3] << '\n';
        return 0;
    }
    if (mode == "exit") return 7;
    if (mode == "tree" || mode == "orphan") {
        const auto child = ::fork();
        require(child >= 0, "fork failed");
        if (child == 0) { for (;;) ::pause(); }
        std::cout << child << std::endl;
        if (mode == "orphan") return 0;
    }
    if (mode == "closed") ::close(STDOUT_FILENO);
    if (mode == "flood") {
        for (;;) std::cout << std::string(4096, 'x') << std::flush;
    }
    for (;;) ::pause();
}
}
int main(int argc, char** argv) {
    try {
        if (argc > 1) return helper(argv[1], argc, argv);
        // Adopt grandchildren so the test also proves their final status without depending on init's reaper.
        require(::prctl(PR_SET_CHILD_SUBREAPER, 1) == 0, "cannot adopt helper descendants");
        const auto self = fs::read_symlink("/proc/self/exe");
        std::atomic<bool> cancelled{false};
        const auto accept = [](std::string_view) { return true; };
        const int sentinel = ::open("/dev/null", O_RDONLY);
        require(sentinel >= 3, "cannot open inheritance sentinel");
        std::string output;
        const std::string utf8 = "path with spaces/\xc3\xa9-\xe6\x98\x9f\\\"$";
        require(run_posix_helper({self, "echo", std::to_string(sentinel), utf8}, cancelled,
            [&](std::string_view bytes) { output += bytes; return true; }) == 0, "echo failed");
        ::close(sentinel);
        require(output == utf8 + '\n', "stdout/UTF-8 argv changed or stderr leaked");
        require(run_posix_helper({self, "exit"}, cancelled, accept, 2s) == 7, "exit code lost");
        bool failed = false;
        try { run_posix_helper({self.parent_path() / "missing-eawr-helper"}, cancelled, accept, 2s); }
        catch (const std::runtime_error&) { failed = true; }
        require(failed, "exec failure accepted");
        for (const auto* mode : {"closed", "flood"}) {
            bool timed_out = false;
            const auto started = std::chrono::steady_clock::now();
            try { run_posix_helper({self, mode}, cancelled, accept, 150ms); }
            catch (const std::runtime_error& e) { timed_out = std::string_view(e.what()).find("timed out") != std::string_view::npos; }
            require(timed_out, "timeout failed with closed or flooding stdout");
            require(std::chrono::steady_clock::now() - started < 2s, "timeout cleanup stalled");
        }
        std::jthread cancel([&] { std::this_thread::sleep_for(100ms); cancelled = true; });
        const auto cancelled_code = run_posix_helper({self, "closed"}, cancelled, accept);
        cancel.join();
        require(cancelled_code == -1, "cancellation after stdout EOF ignored");
        cancelled = false;
        for (const auto* mode : {"tree", "orphan"}) {
            output.clear();
            const bool orphan = std::string_view(mode) == "orphan";
            const auto code = run_posix_helper({self, mode}, cancelled, [&](std::string_view bytes) {
                output += bytes;
                if (!orphan && output.find('\n') != std::string::npos) cancelled = true;
                return true;
            }, 2s);
            require(code == (orphan ? 0 : -1), "wrong cancellation/exit status");
            int status{};
            const auto descendant = static_cast<pid_t>(std::stoi(output));
            require(::waitpid(descendant, &status, 0) == descendant && WIFSIGNALED(status)
                && WTERMSIG(status) == SIGKILL, "descendant survived process group cleanup");
            cancelled = false;
        }
        require(run_posix_helper({self, "flood"}, cancelled,
            [](std::string_view) { return false; }, 2s) == -1, "rejected output did not stop helper");
        cancelled = true;
        require(run_posix_helper({self, "closed"}, cancelled, accept, 2s) == -1, "pre-spawn cancellation ignored");
        require(::waitpid(-1, nullptr, WNOHANG) == -1 && errno == ECHILD, "unreaped helper");
        std::cout << "POSIX helper transport and lifecycle passed\n";
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
}
