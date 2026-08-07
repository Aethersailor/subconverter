#ifndef SHA256_H_INCLUDED
#define SHA256_H_INCLUDED

#include <filesystem>
#include <optional>
#include <string>

// Returns a lowercase, unprefixed SHA-256 digest. I/O failures return nullopt.
std::optional<std::string> sha256File(const std::filesystem::path &path);

#endif // SHA256_H_INCLUDED
