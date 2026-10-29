#pragma once
// The path helpers moved to src/mods/steam (shared with the mod core); the launcher keeps its names.
#include "eawr/mods/steam/paths.hpp"
#ifndef _WIN32
#include <iconv.h>
#include <cstring>
#include <stdexcept>
#endif

namespace eawr::launcher {

namespace fs = std::filesystem;

using mods::steam::find_child_ci;
using mods::steam::lower_ascii;
using mods::steam::path_from_utf8;
using mods::steam::path_to_utf8;
using mods::steam::same_location;
using mods::steam::widen_ascii;

#ifndef _WIN32
inline std::string convert_encoding(const char* data, std::size_t size, const char* from, const char* to) {
    const iconv_t converter = iconv_open(to, from);
    if (converter == reinterpret_cast<iconv_t>(-1)) throw std::runtime_error("Cannot initialize UTF-8 conversion.");
    std::string result;
    try { result.resize(size * 4 + 4); }
    catch (...) { iconv_close(converter); throw; }
    char* input = const_cast<char*>(data);
    char* output = result.data();
    std::size_t available = result.size();
    const auto converted = iconv(converter, &input, &size, &output, &available);
    iconv_close(converter);
    if (converted == static_cast<std::size_t>(-1) || size != 0)
        throw std::invalid_argument("Invalid UTF-8 or wide-character text.");
    result.resize(result.size() - available);
    return result;
}
#endif

// filesystem::wstring uses the C locale on Linux, not the UTF-8 filesystem encoding.
inline std::wstring path_to_wide(const fs::path& path) {
#ifdef _WIN32
    return path.wstring();
#else
    const auto bytes = convert_encoding(path.native().data(), path.native().size(), "UTF-8", "WCHAR_T");
    std::wstring result(bytes.size() / sizeof(wchar_t), L'\0');
    std::memcpy(result.data(), bytes.data(), bytes.size());
    return result;
#endif
}

inline fs::path path_from_wide(const std::wstring& text) {
#ifdef _WIN32
    return fs::path(text);
#else
    return fs::path(convert_encoding(reinterpret_cast<const char*>(text.data()), text.size() * sizeof(wchar_t), "WCHAR_T", "UTF-8"));
#endif
}

inline constexpr const char* kModsServiceExecutableName =
#ifdef _WIN32
    "eawr_mods_service.exe";
#else
    "eawr_mods_service";
#endif

} // namespace eawr::launcher
