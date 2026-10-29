#include "linux_platform.hpp"

#include <cerrno>
#include <chrono>
#include <cstring>
#include <fcntl.h>
#include <sys/wait.h>
#include <unistd.h>

namespace eawr::launcher {
namespace {
class LinuxProcess final : public ProcessRunner {
    ChildResult run(const fs::path& exe, const std::vector<std::wstring>& args, const fs::path& cwd) override {
        ChildResult result;
        std::vector<std::string> storage{path_to_utf8(fs::absolute(exe))};
        try {
            for (const auto& arg : args) storage.push_back(path_to_utf8(path_from_wide(arg)));
        } catch (const std::exception& error) {
            result.error = error.what();
            return result;
        }
        std::vector<char*> argv;
        for (auto& arg : storage) {
            if (arg.find('\0') != std::string::npos) {
                result.error = "A process argument contains a NUL character.";
                return result;
            }
            argv.push_back(arg.data());
        }
        argv.push_back(nullptr);
        int pipefd[2];
        if (pipe2(pipefd, O_CLOEXEC) != 0) {
            result.error = std::strerror(errno);
            return result;
        }
        const auto start = std::chrono::steady_clock::now();
        const pid_t pid = fork();
        if (pid == 0) {
            close(pipefd[0]);
            if (chdir(cwd.c_str()) == 0) execv(argv[0], argv.data());
            const int failure = errno;
            // Only async-signal-safe calls between fork and exec (GTK can have threads).
            ssize_t written;
            do { written = write(pipefd[1], &failure, sizeof failure); } while (written < 0 && errno == EINTR);
            _exit(127);
        }
        const int fork_error = errno;
        close(pipefd[1]);
        if (pid < 0) {
            close(pipefd[0]);
            result.error = std::strerror(fork_error);
            return result;
        }
        int failure = 0;
        ssize_t received;
        do { received = read(pipefd[0], &failure, sizeof failure); } while (received < 0 && errno == EINTR);
        const int read_error = errno;
        close(pipefd[0]);
        int status = 0;
        pid_t waited;
        do { waited = waitpid(pid, &status, 0); } while (waited < 0 && errno == EINTR);
        const int wait_error = errno;
        result.seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
        if (received != 0 || waited < 0) {
            result.error = std::strerror(received > 0 ? failure : received < 0 ? read_error : wait_error);
            return result;
        }
        result.started = true;
        result.exit_code = WIFEXITED(status) ? static_cast<unsigned>(WEXITSTATUS(status))
                                           : static_cast<unsigned>(128 + WTERMSIG(status));
        return result;
    }
};

class NoRegistry final : public RegistryReader {
    std::optional<std::string> read_string(RegistryHive, RegistryView, const std::string&, const std::string&) const override { return {}; }
    std::vector<std::string> subkeys(RegistryHive, RegistryView, const std::string&) const override { return {}; }
};
}
std::unique_ptr<ProcessRunner> make_linux_process_runner() { return std::make_unique<LinuxProcess>(); }
std::unique_ptr<RegistryReader> make_linux_registry() { return std::make_unique<NoRegistry>(); }
}
