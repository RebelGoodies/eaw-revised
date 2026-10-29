// Synthetic process seam: the service starts and contains this exactly as the real engine helper.
#include "eawr/mods/file_index.hpp"
#ifdef _WIN32
#include <windows.h>
#include <shellapi.h>
#else
#include <unistd.h>
#include <iostream>
#endif
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>
#include <thread>
#include <chrono>
void emit(const std::string& text) {
#ifdef _WIN32
    auto line = text + '\n';
    while (!line.empty()) {
        DWORD count{};
        if (!WriteFile(GetStdHandle(STD_OUTPUT_HANDLE), line.data(), static_cast<DWORD>(line.size()), &count, nullptr) || !count) return;
        line.erase(0, count);
    }
#else
    std::cout << text << std::endl;
#endif
}
int run(const std::vector<std::filesystem::path>& argv) {
    const auto argc = argv.size();
    if (argc != 5 && argc != 7) return 2;
    std::ifstream input{std::filesystem::path(argv[1])};
    std::string profile((std::istreambuf_iterator<char>(input)), {});
    const auto user = std::filesystem::path(argv[2]);
    // An observable PID proves that cancel/shutdown killed the child.
#ifdef _WIN32
    std::ofstream{user / "helper.pid"} << GetCurrentProcessId();
#else
    std::ofstream{user / "helper.pid"} << ::getpid();
#endif
    if (argc == 7 && profile.find("file-index") != std::string::npos) {
        eawr::mods::FileIndex index;
        index.chain_fingerprint = std::filesystem::path(argv[5]).string();
        index.layers = {{"mods:Leaf"}, {"expansion"}, {"base"}};
        using Candidate = eawr::mods::FileCandidate;
        index.rows = {
            {"data/art/only.alo", {Candidate{"mods:Leaf", "loose", "only", 100, {}}}},
            {"data/xml/changed.xml", {Candidate{"mods:Leaf", "loose", "leaf", 3, std::string(64, 'a')}, Candidate{"expansion", "archive", "fixture.meg", 4, std::string(64, 'b')}}},
            {"data/xml/same.xml", {Candidate{"mods:Leaf", "archive", "mod.meg", 4, std::string(64, 'c')}, Candidate{"base", "loose", "base", 4, std::string(64, 'c')}}}};
        eawr::mods::summarize_file_index(index);
        std::ofstream cache{std::filesystem::path(argv[6]), std::ios::binary};
        if (profile.find("partial-index") != std::string::npos) cache << "EAWR-FILES\n";
        else if (!eawr::mods::write_file_index(cache, index)) return 3;
    }
#ifdef _WIN32
    DWORD logged{};
    constexpr char log[] = "synthetic helper log\n";
    WriteFile(GetStdHandle(STD_ERROR_HANDLE), log, sizeof(log) - 1, &logged, nullptr);
#else
    std::cerr << "synthetic helper log\n";
#endif
    emit("{\"stage\":\"mount\",\"done\":0,\"total\":5}");
    if (profile.find("timed") != std::string::npos) std::this_thread::sleep_for(std::chrono::milliseconds(150));
    if (profile.find("slow") != std::string::npos) std::this_thread::sleep_for(std::chrono::seconds(60));
    if (profile.find("crash") != std::string::npos) return 3;
#ifndef _WIN32
    if (profile.find("unterminated") != std::string::npos) { std::cout << "{\"report\":"; return 0; }
#endif
    if (profile.find("nonjson") != std::string::npos) { emit("not JSON"); return 0; }
    if (profile.find("invalid") != std::string::npos) { emit("{\"report\":{\"stage\":\"invalid\"}}"); return 0; }
    if (profile.find("badseverity") != std::string::npos || profile.find("badtotals") != std::string::npos) {
        const auto severity = profile.find("badseverity") != std::string::npos ? "unknown" : "error";
        emit(std::string("{\"report\":{\"stage\":\"complete\",\"ok\":false,\"error_count\":0,\"warning_count\":0,\"content_code\":\"\",\"requirements\":[],\"diagnostics\":[{\"code\":\"synthetic\",\"severity\":\"")
            + severity + "\",\"count\":1,\"examples\":[]}]}}");
        return 0;
    }
    if (profile.find("oversize") != std::string::npos) { emit(std::string(1024 * 1024 + 1, 'x')); return 0; }
    if (profile.find("wrapped") != std::string::npos || profile.find("near-limit") != std::string::npos
        || profile.find("large-valid") != std::string::npos) {
        std::string report = "{\"stage\":\"complete\",\"ok\":true,\"error_count\":0,\"warning_count\":0,\"content_code\":\"ABCDEFGHIJ\",\"requirements\":[],\"diagnostics\":[],\"padding\":\"\"}";
        const std::size_t reserve = profile.find("large-valid") != std::string::npos ? 512U
            : (profile.find("near-limit") != std::string::npos ? 128U : 11U);
        report.insert(report.size() - 2, 1024 * 1024 - reserve - report.size(), 'x');
        emit("{\"report\":" + report + '}');
        return 0;
    }
    if (profile.find("changed") != std::string::npos) {
        for (int i = 0; i < 1000 && !std::filesystem::exists(user / "continue"); ++i)
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        if (!std::filesystem::exists(user / "continue")) return 3;
    }
    const bool diagnostic = profile.find("diagnostic") != std::string::npos;
    emit("{\"stage\":\"complete\",\"done\":5,\"total\":5}");
    emit(std::string("{\"report\":{\"scope\":\"space-skirmish\",\"stage\":\"complete\",\"ok\":true,\"content_code\":\"ABCDEFGHIJ\",\"requirements\":[{\"kind\":\"foc\",\"key\":\"foc\",\"satisfied\":true}],\"error_count\":0,\"warning_count\":") +
        (diagnostic ? "5" : "0") + ",\"diagnostics\":" +
        (diagnostic ? "[{\"code\":\"EAWR-UNITS-0001\",\"severity\":\"warning\",\"count\":4,\"examples\":[\"one\",\"two\",\"three\"]},{\"code\":\"EAWR-UNITS-0002\",\"severity\":\"warning\",\"count\":1,\"examples\":[\"four\"]}]" : "[]") + "}}");
    return 0;
}
#ifdef _WIN32
int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    int argc{}; auto argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    std::vector<std::filesystem::path> args;
    for (int i = 0; argv && i < argc; ++i) args.emplace_back(argv[i]);
    if (argv) LocalFree(argv);
    return run(args);
}
#else
int main(int argc, char** argv) {
    std::vector<std::filesystem::path> args;
    for (int i = 0; i < argc; ++i) args.emplace_back(argv[i]);
    return run(args);
}
#endif
