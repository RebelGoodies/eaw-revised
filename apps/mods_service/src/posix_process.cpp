#include "posix_process.hpp"
#include <array>
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <poll.h>
#include <spawn.h>
#include <stdexcept>
#include <sys/wait.h>
#include <unistd.h>

extern char** environ;

namespace eawr::mods_service {
namespace {
struct Fd {
    int value{-1};
    ~Fd() { if (value >= 0) ::close(value); }
};
struct Child {
    pid_t pid{-1};
    ~Child() {
        if (pid <= 0) return;
        // Keep the leader unreaped until after killing its group, so its PID cannot be reused.
        ::kill(-pid, SIGKILL);
        while (::waitpid(pid, nullptr, 0) < 0 && errno == EINTR) {}
    }
};
struct Spawn {
    posix_spawn_file_actions_t actions;
    posix_spawnattr_t attributes;
    Spawn() {
        if (posix_spawn_file_actions_init(&actions)) throw std::runtime_error("cannot initialize helper handles");
        if (posix_spawnattr_init(&attributes)) {
            posix_spawn_file_actions_destroy(&actions);
            throw std::runtime_error("cannot initialize helper process group");
        }
    }
    ~Spawn() { posix_spawnattr_destroy(&attributes); posix_spawn_file_actions_destroy(&actions); }
};
}
int run_posix_helper(const std::vector<std::filesystem::path>& args,
                     const std::atomic<bool>& cancelled,
                     const std::function<bool(std::string_view)>& consume,
                     std::optional<std::chrono::milliseconds> timeout) {
    if (args.empty()) throw std::runtime_error("missing load check helper");
    int pipe[2];
    if (::pipe2(pipe, O_CLOEXEC)) throw std::runtime_error("cannot create helper pipe");
    Fd read{pipe[0]}, write{pipe[1]}, sink{::open("/dev/null", O_RDWR | O_CLOEXEC)};
    if (sink.value < 0 || ::fcntl(read.value, F_SETFL, O_NONBLOCK) < 0)
        throw std::runtime_error("cannot initialize helper pipe");
    // Native Linux path bytes are UTF-8, with no shell expansion or argv quoting.
    std::vector<char*> argv;
    for (const auto& arg : args) argv.push_back(const_cast<char*>(arg.c_str()));
    argv.push_back(nullptr);
    Spawn spawn;
    if (posix_spawn_file_actions_adddup2(&spawn.actions, sink.value, STDIN_FILENO)
        || posix_spawn_file_actions_adddup2(&spawn.actions, write.value, STDOUT_FILENO)
        || posix_spawn_file_actions_adddup2(&spawn.actions, sink.value, STDERR_FILENO)
        || posix_spawn_file_actions_addclosefrom_np(&spawn.actions, 3)
        || posix_spawnattr_setflags(&spawn.attributes, POSIX_SPAWN_SETPGROUP)
        || posix_spawnattr_setpgroup(&spawn.attributes, 0))
        throw std::runtime_error("cannot contain load check helper");
    if (cancelled) return -1;
    Child child;
    const auto started = std::chrono::steady_clock::now();
    if (posix_spawn(&child.pid, args.front().c_str(), &spawn.actions, &spawn.attributes, argv.data(), environ)) {
        child.pid = -1;
        throw std::runtime_error("cannot start load check helper");
    }
    ::close(write.value); write.value = -1;
    bool eof = false;
    for (;;) {
        if (cancelled) return -1;
        if (timeout && std::chrono::steady_clock::now() - started >= *timeout)
            throw std::runtime_error("load check helper timed out");
        siginfo_t status{};
        int waited;
        do { waited = ::waitid(P_PID, static_cast<id_t>(child.pid), &status, WEXITED | WNOHANG | WNOWAIT); }
        while (waited < 0 && errno == EINTR);
        if (waited < 0) throw std::runtime_error("cannot wait for load check helper");
        const bool exited = status.si_pid != 0;
        // Descendants must not keep stdout open after their leader exits.
        if (exited) ::kill(-child.pid, SIGKILL);
        if (!eof) {
            pollfd ready{read.value, POLLIN, 0};
            const auto polled = ::poll(&ready, 1, exited ? 0 : 50);
            if (polled < 0 && errno == EINTR) continue;
            if (polled < 0 || (ready.revents & POLLNVAL)) throw std::runtime_error("cannot read helper pipe");
            if (polled > 0 || exited) {
                std::array<char, 4096> bytes{};
                // Bound each drain so continuously writing children cannot starve timeout/cancel.
                for (int i = 0; i < 16; ++i) {
                    const auto got = ::read(read.value, bytes.data(), bytes.size());
                    if (got > 0) { if (!consume({bytes.data(), static_cast<std::size_t>(got)})) return -1; }
                    else if (got == 0) { eof = true; break; }
                    else if (errno == EINTR) continue;
                    else if (errno == EAGAIN || errno == EWOULDBLOCK) break;
                    else throw std::runtime_error("cannot read helper pipe");
                }
            }
        } else if (!exited) {
            ::poll(nullptr, 0, 50); // Closing stdout does not imply that the helper has exited.
        }
        if (exited && eof) return status.si_code == CLD_EXITED ? status.si_status : -1;
    }
}
}
