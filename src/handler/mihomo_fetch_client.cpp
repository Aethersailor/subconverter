#include "handler/mihomo_fetch_client.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <limits>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

#include "handler/settings.h"
#include "utils/logger.h"
#include "utils/sha256.h"

#ifdef _WIN32
#include <windows.h>
#elif defined(__APPLE__)
#include <fcntl.h>
#include <mach-o/dyld.h>
#include <poll.h>
#include <spawn.h>
#include <signal.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#else
#include <fcntl.h>
#include <poll.h>
#include <spawn.h>
#include <signal.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

#ifndef SUBCONVERTER_MIHOMO_VERSION
#define SUBCONVERTER_MIHOMO_VERSION ""
#endif

#ifndef SUBCONVERTER_MIHOMO_COMMIT
#define SUBCONVERTER_MIHOMO_COMMIT ""
#endif

#ifndef SUBCONVERTER_MIHOMO_OVERLAY_SHA256
#define SUBCONVERTER_MIHOMO_OVERLAY_SHA256 ""
#endif

#ifndef SUBCONVERTER_MIHOMO_PAIR_ID
#define SUBCONVERTER_MIHOMO_PAIR_ID ""
#endif

#ifndef SUBCONVERTER_MIHOMO_PROTOCOL
#define SUBCONVERTER_MIHOMO_PROTOCOL ""
#endif

#ifndef SUBCONVERTER_MIHOMO_PARITY_CONTRACT
#define SUBCONVERTER_MIHOMO_PARITY_CONTRACT ""
#endif

#ifndef SUBCONVERTER_MIHOMO_HELPER_SHA256
#define SUBCONVERTER_MIHOMO_HELPER_SHA256 ""
#endif

#ifndef SUBCONVERTER_MIHOMO_HELPER_PLATFORM
#define SUBCONVERTER_MIHOMO_HELPER_PLATFORM ""
#endif

#ifndef SUBCONVERTER_MIHOMO_HELPER_NAME
#define SUBCONVERTER_MIHOMO_HELPER_NAME ""
#endif

#ifndef _WIN32
extern char **environ;
#endif

namespace
{
using json = nlohmann::json;

constexpr std::uint64_t protocol_version = 1;
constexpr std::uint32_t max_request_frame = 4U << 20;
constexpr std::uint32_t max_response_frame = 128U << 20;
constexpr std::uintmax_t max_manifest_size = 1U << 20;
constexpr auto startup_timeout = std::chrono::seconds(5);
constexpr int max_fetch_attempts = 2;

bool isTransientFetchError(const std::string &error_code)
{
    return error_code == "timeout" || error_code == "fetch_failed";
}

std::string runtimePlatform()
{
#if defined(_WIN32)
#if defined(_M_X64) || defined(__x86_64__)
    return "windows-amd64";
#elif defined(_M_IX86) || defined(__i386__)
    return "windows-386";
#else
    return {};
#endif
#elif defined(__APPLE__)
#if defined(__x86_64__)
    return "macos-amd64";
#elif defined(__aarch64__) || defined(__arm64__)
    return "macos-arm64";
#else
    return {};
#endif
#else
#if defined(__x86_64__)
    return "linux-amd64";
#elif defined(__i386__)
    return "linux-386";
#elif defined(__aarch64__)
    return "linux-arm64";
#elif defined(__arm__)
    return "linux-armv7";
#else
    return {};
#endif
#endif
}

std::pair<std::string, std::string> runtimeGoTarget()
{
    const auto platform = runtimePlatform();
    if(platform == "linux-amd64") return {"linux", "amd64"};
    if(platform == "linux-386") return {"linux", "386"};
    if(platform == "linux-arm64") return {"linux", "arm64"};
    if(platform == "linux-armv7") return {"linux", "arm"};
    if(platform == "macos-amd64") return {"darwin", "amd64"};
    if(platform == "macos-arm64") return {"darwin", "arm64"};
    if(platform == "windows-amd64") return {"windows", "amd64"};
    if(platform == "windows-386") return {"windows", "386"};
    return {};
}

std::filesystem::path executableDirectory()
{
#ifdef _WIN32
    std::wstring path(32768, L'\0');
    const DWORD length = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if(length == 0 || length >= path.size())
        return std::filesystem::current_path();
    path.resize(length);
    return std::filesystem::path(path).parent_path();
#elif defined(__APPLE__)
    std::uint32_t length = 0;
    _NSGetExecutablePath(nullptr, &length);
    std::vector<char> path(length + 1, '\0');
    if(_NSGetExecutablePath(path.data(), &length) != 0)
        return std::filesystem::current_path();
    return std::filesystem::weakly_canonical(path.data()).parent_path();
#else
    std::error_code error;
    const auto path = std::filesystem::read_symlink("/proc/self/exe", error);
    if(error)
        return std::filesystem::current_path();
    return path.parent_path();
#endif
}

std::optional<std::filesystem::path> locateHelper()
{
    if(const char *configured = std::getenv("SUBCONVERTER_MIHOMO_FETCHER_PATH"); configured && *configured)
    {
        std::filesystem::path path(configured);
        if(std::filesystem::is_regular_file(path))
            return std::filesystem::absolute(path);
        return std::nullopt;
    }

#ifdef _WIN32
    constexpr const char *helper_name = "subconverter-mihomo-fetcher.exe";
#else
    constexpr const char *helper_name = "subconverter-mihomo-fetcher";
#endif
    const auto executable_dir = executableDirectory();
    const std::array candidates = {
        executable_dir / helper_name,
        executable_dir / "libexec" / helper_name,
        std::filesystem::current_path() / helper_name,
#ifndef _WIN32
        std::filesystem::path("/usr/libexec/subconverter") / helper_name,
#else
        executable_dir / ".." / "libexec" / helper_name,
#endif
    };
    for(const auto &candidate : candidates)
    {
        std::error_code error;
        if(std::filesystem::is_regular_file(candidate, error) && !error)
            return std::filesystem::weakly_canonical(candidate, error);
    }
    return std::nullopt;
}

std::optional<std::filesystem::path> locateManifest(const std::filesystem::path &helper)
{
    if(const char *configured = std::getenv("SUBCONVERTER_MIHOMO_FETCHER_MANIFEST_PATH"); configured && *configured)
    {
        std::filesystem::path path(configured);
        std::error_code error;
        if(std::filesystem::is_regular_file(path, error) && !error)
            return std::filesystem::weakly_canonical(path, error);
        return std::nullopt;
    }

    const std::array candidates = {
        helper.parent_path() / "subconverter-mihomo-fetcher.manifest.json",
#ifndef _WIN32
        std::filesystem::path("/usr/share/subconverter/subconverter-mihomo-fetcher.manifest.json"),
#else
        executableDirectory() / "subconverter-mihomo-fetcher.manifest.json",
#endif
    };
    for(const auto &candidate : candidates)
    {
        std::error_code error;
        if(std::filesystem::is_regular_file(candidate, error) && !error)
            return std::filesystem::weakly_canonical(candidate, error);
    }
    return std::nullopt;
}

bool stringFieldEquals(const json &object, const char *field, const std::string &expected)
{
    const auto iterator = object.find(field);
    return iterator != object.end() && iterator->is_string() && iterator->get<std::string>() == expected;
}

bool validateHelperIdentity(const std::filesystem::path &helper, const std::filesystem::path &manifest_path)
{
    const std::string expected_pair = SUBCONVERTER_MIHOMO_PAIR_ID;
    const std::string expected_tag = SUBCONVERTER_MIHOMO_VERSION;
    const std::string expected_commit = SUBCONVERTER_MIHOMO_COMMIT;
    const std::string expected_overlay = SUBCONVERTER_MIHOMO_OVERLAY_SHA256;
    const std::string expected_protocol = SUBCONVERTER_MIHOMO_PROTOCOL;
    const std::string expected_parity = SUBCONVERTER_MIHOMO_PARITY_CONTRACT;
    const std::string expected_digest = SUBCONVERTER_MIHOMO_HELPER_SHA256;
    const std::string expected_platform = SUBCONVERTER_MIHOMO_HELPER_PLATFORM;
    const std::string expected_name = SUBCONVERTER_MIHOMO_HELPER_NAME;
    if(expected_pair.empty() || expected_tag.empty() || expected_commit.empty() || expected_overlay.empty() ||
       expected_protocol != std::to_string(protocol_version) || expected_parity.empty() ||
       expected_digest.empty() || expected_platform.empty() || expected_name.empty() ||
       expected_platform != runtimePlatform() || helper.filename().string() != expected_name)
        return false;

    std::error_code manifest_error;
    const auto manifest_size = std::filesystem::file_size(manifest_path, manifest_error);
    if(manifest_error || manifest_size == 0 || manifest_size > max_manifest_size)
        return false;
    std::ifstream manifest_stream(manifest_path, std::ios::binary);
    if(!manifest_stream)
        return false;
    try
    {
        json manifest;
        manifest_stream >> manifest;
        if(!manifest.is_object() || !manifest.contains("schema_version") ||
           !manifest["schema_version"].is_number_integer() || manifest["schema_version"].get<int>() != 1 ||
           !stringFieldEquals(manifest, "pair_id", expected_pair) ||
           !stringFieldEquals(manifest, "platform", expected_platform))
            return false;

        const auto mihomo = manifest.find("mihomo");
        const auto project = manifest.find("project");
        const auto helper_identity = manifest.find("helper");
        if(mihomo == manifest.end() || !mihomo->is_object() ||
           project == manifest.end() || !project->is_object() ||
           helper_identity == manifest.end() || !helper_identity->is_object())
            return false;
        if(!stringFieldEquals(*mihomo, "tag", expected_tag) ||
           !stringFieldEquals(*mihomo, "commit", expected_commit) ||
           !stringFieldEquals(*project, "helper_overlay_sha256", expected_overlay) ||
           !stringFieldEquals(*project, "parity_contract", expected_parity))
            return false;
        const auto project_protocol = project->find("helper_protocol");
        if(project_protocol == project->end() || !project_protocol->is_number_integer() ||
           project_protocol->get<std::uint64_t>() != protocol_version)
            return false;
        if(!stringFieldEquals(*helper_identity, "name", expected_name) ||
           !stringFieldEquals(*helper_identity, "sha256", expected_digest))
            return false;

        std::error_code error;
        const auto actual_size = std::filesystem::file_size(helper, error);
        const auto declared_size = helper_identity->find("size");
        if(error || declared_size == helper_identity->end() || !declared_size->is_number_unsigned() ||
           declared_size->get<std::uint64_t>() != actual_size)
            return false;
        const auto digest = sha256File(helper);
        return digest && "sha256:" + *digest == expected_digest;
    }
    catch(const std::exception &)
    {
        return false;
    }
}

bool exactCapabilities(const json &value)
{
    static const std::set<std::string> expected = {
        "direct", "etag", "http-proxy", "https-proxy", "raw-body", "response-headers", "socks5-proxy",
    };
    if(!value.is_array() || value.size() != expected.size())
        return false;
    std::set<std::string> actual;
    for(const auto &capability : value)
    {
        if(!capability.is_string())
            return false;
        actual.insert(capability.get<std::string>());
    }
    return actual == expected;
}

class HelperProcess
{
public:
    HelperProcess() = default;
    HelperProcess(const HelperProcess &) = delete;
    HelperProcess &operator=(const HelperProcess &) = delete;
    ~HelperProcess()
    {
        stop();
    }

    bool start(const std::filesystem::path &path)
    {
        stop();
#ifdef _WIN32
        SECURITY_ATTRIBUTES attributes{};
        attributes.nLength = sizeof(attributes);
        attributes.bInheritHandle = TRUE;

        HANDLE child_stdin_read = nullptr;
        HANDLE child_stdout_write = nullptr;
        HANDLE child_stderr_write = nullptr;
        if(!CreatePipe(&child_stdin_read, &stdin_write_, &attributes, 0) ||
           !SetHandleInformation(stdin_write_, HANDLE_FLAG_INHERIT, 0) ||
           !CreatePipe(&stdout_read_, &child_stdout_write, &attributes, 0) ||
           !SetHandleInformation(stdout_read_, HANDLE_FLAG_INHERIT, 0))
        {
            if(child_stdin_read) CloseHandle(child_stdin_read);
            if(child_stdout_write) CloseHandle(child_stdout_write);
            stop();
            return false;
        }

        const HANDLE current_stderr = GetStdHandle(STD_ERROR_HANDLE);
        if(!current_stderr || current_stderr == INVALID_HANDLE_VALUE ||
           !DuplicateHandle(GetCurrentProcess(), current_stderr, GetCurrentProcess(), &child_stderr_write,
                            0, TRUE, DUPLICATE_SAME_ACCESS))
        {
            child_stderr_write = CreateFileW(L"NUL", GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                             &attributes, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        }
        if(!child_stderr_write || child_stderr_write == INVALID_HANDLE_VALUE)
        {
            CloseHandle(child_stdin_read);
            CloseHandle(child_stdout_write);
            stop();
            return false;
        }

        SIZE_T attribute_size = 0;
        InitializeProcThreadAttributeList(nullptr, 1, 0, &attribute_size);
        std::vector<unsigned char> attribute_storage(attribute_size);
        STARTUPINFOEXW startup{};
        startup.StartupInfo.cb = sizeof(startup);
        startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        startup.StartupInfo.hStdInput = child_stdin_read;
        startup.StartupInfo.hStdOutput = child_stdout_write;
        startup.StartupInfo.hStdError = child_stderr_write;
        startup.lpAttributeList = reinterpret_cast<PPROC_THREAD_ATTRIBUTE_LIST>(attribute_storage.data());
        const std::array<HANDLE, 3> inherited_handles = {
            child_stdin_read, child_stdout_write, child_stderr_write,
        };
        const bool attributes_initialized = attribute_size != 0 &&
            InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, &attribute_size);
        if(!attributes_initialized ||
           !UpdateProcThreadAttribute(startup.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                      const_cast<HANDLE *>(inherited_handles.data()),
                                      sizeof(inherited_handles), nullptr, nullptr))
        {
            if(attributes_initialized)
                DeleteProcThreadAttributeList(startup.lpAttributeList);
            CloseHandle(child_stdin_read);
            CloseHandle(child_stdout_write);
            CloseHandle(child_stderr_write);
            stop();
            return false;
        }

        PROCESS_INFORMATION process{};
        std::wstring command = L"\"" + path.wstring() + L"\"";
        const BOOL created = CreateProcessW(
            nullptr,
            command.data(),
            nullptr,
            nullptr,
            TRUE,
            CREATE_NO_WINDOW | EXTENDED_STARTUPINFO_PRESENT,
            nullptr,
            path.parent_path().wstring().c_str(),
            &startup.StartupInfo,
            &process);
        DeleteProcThreadAttributeList(startup.lpAttributeList);
        CloseHandle(child_stdin_read);
        CloseHandle(child_stdout_write);
        CloseHandle(child_stderr_write);
        if(!created)
        {
            stop();
            return false;
        }
        process_ = process.hProcess;
        CloseHandle(process.hThread);
#else
        int stdin_pipe[2] {-1, -1};
        int stdout_pipe[2] {-1, -1};
        if(!createCloseOnExecPipe(stdin_pipe) || !createCloseOnExecPipe(stdout_pipe))
        {
            if(stdin_pipe[0] >= 0) close(stdin_pipe[0]);
            if(stdin_pipe[1] >= 0) close(stdin_pipe[1]);
            if(stdout_pipe[0] >= 0) close(stdout_pipe[0]);
            if(stdout_pipe[1] >= 0) close(stdout_pipe[1]);
            return false;
        }

        posix_spawn_file_actions_t actions;
        int spawn_error = posix_spawn_file_actions_init(&actions);
        const bool actions_initialized = spawn_error == 0;
        if(spawn_error == 0) spawn_error = posix_spawn_file_actions_adddup2(&actions, stdin_pipe[0], STDIN_FILENO);
        if(spawn_error == 0) spawn_error = posix_spawn_file_actions_adddup2(&actions, stdout_pipe[1], STDOUT_FILENO);
        if(spawn_error == 0) spawn_error = posix_spawn_file_actions_addclose(&actions, stdin_pipe[0]);
        if(spawn_error == 0) spawn_error = posix_spawn_file_actions_addclose(&actions, stdin_pipe[1]);
        if(spawn_error == 0) spawn_error = posix_spawn_file_actions_addclose(&actions, stdout_pipe[0]);
        if(spawn_error == 0) spawn_error = posix_spawn_file_actions_addclose(&actions, stdout_pipe[1]);
        const std::string native = path.string();
        std::array<char *, 2> arguments = {const_cast<char *>(native.c_str()), nullptr};
        pid_t child = -1;
        if(spawn_error == 0)
            spawn_error = posix_spawn(&child, native.c_str(), &actions, nullptr, arguments.data(), environ);
        if(actions_initialized)
            posix_spawn_file_actions_destroy(&actions);
        if(spawn_error != 0)
        {
            close(stdin_pipe[0]);
            close(stdin_pipe[1]);
            close(stdout_pipe[0]);
            close(stdout_pipe[1]);
            return false;
        }

        child_pid_ = child;
        stdin_write_ = stdin_pipe[1];
        stdout_read_ = stdout_pipe[0];
        close(stdin_pipe[0]);
        close(stdout_pipe[1]);
#endif
        return true;
    }

    void stop()
    {
#ifdef _WIN32
        if(stdin_write_)
        {
            CloseHandle(stdin_write_);
            stdin_write_ = nullptr;
        }
        if(stdout_read_)
        {
            CloseHandle(stdout_read_);
            stdout_read_ = nullptr;
        }
        if(process_)
        {
            if(WaitForSingleObject(process_, 250) == WAIT_TIMEOUT)
            {
                TerminateProcess(process_, 1);
                WaitForSingleObject(process_, 1000);
            }
            CloseHandle(process_);
            process_ = nullptr;
        }
#else
        if(stdin_write_ >= 0)
        {
            close(stdin_write_);
            stdin_write_ = -1;
        }
        if(stdout_read_ >= 0)
        {
            close(stdout_read_);
            stdout_read_ = -1;
        }
        if(child_pid_ > 0)
        {
            if(!waitForExit(child_pid_, std::chrono::milliseconds(100)))
            {
                kill(child_pid_, SIGTERM);
                if(!waitForExit(child_pid_, std::chrono::milliseconds(500)))
                {
                    kill(child_pid_, SIGKILL);
                    waitForExit(child_pid_, std::chrono::milliseconds(500));
                }
            }
            child_pid_ = -1;
        }
#endif
    }

    bool writeFrame(const std::vector<std::uint8_t> &payload)
    {
        if(payload.empty() || payload.size() > max_request_frame)
            return false;
        std::array<std::uint8_t, 4> size {
            static_cast<std::uint8_t>((payload.size() >> 24) & 0xff),
            static_cast<std::uint8_t>((payload.size() >> 16) & 0xff),
            static_cast<std::uint8_t>((payload.size() >> 8) & 0xff),
            static_cast<std::uint8_t>(payload.size() & 0xff),
        };
        return writeExact(size.data(), size.size()) && writeExact(payload.data(), payload.size());
    }

    std::optional<std::vector<std::uint8_t>> readFrame(std::chrono::milliseconds timeout)
    {
        const auto deadline = std::chrono::steady_clock::now() + timeout;
        std::array<std::uint8_t, 4> size{};
        if(!readExact(size.data(), size.size(), deadline))
            return std::nullopt;
        const auto length = (static_cast<std::uint32_t>(size[0]) << 24) |
                            (static_cast<std::uint32_t>(size[1]) << 16) |
                            (static_cast<std::uint32_t>(size[2]) << 8) |
                            static_cast<std::uint32_t>(size[3]);
        if(length == 0 || length > max_response_frame)
            return std::nullopt;
        std::vector<std::uint8_t> payload(length);
        if(!readExact(payload.data(), payload.size(), deadline))
            return std::nullopt;
        return payload;
    }

private:
#ifndef _WIN32
    static bool createCloseOnExecPipe(int descriptors[2])
    {
        if(pipe(descriptors) != 0)
            return false;
        for(const int descriptor : {descriptors[0], descriptors[1]})
        {
            const int flags = fcntl(descriptor, F_GETFD);
            if(flags == -1 || fcntl(descriptor, F_SETFD, flags | FD_CLOEXEC) == -1)
            {
                close(descriptors[0]);
                close(descriptors[1]);
                descriptors[0] = descriptors[1] = -1;
                return false;
            }
        }
        return true;
    }

    static bool waitForExit(pid_t child, std::chrono::milliseconds timeout)
    {
        const auto deadline = std::chrono::steady_clock::now() + timeout;
        do
        {
            int status = 0;
            const pid_t result = waitpid(child, &status, WNOHANG);
            if(result == child || (result == -1 && errno == ECHILD))
                return true;
            if(result == -1 && errno != EINTR)
                return false;
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        while(std::chrono::steady_clock::now() < deadline);
        return false;
    }
#endif

    bool writeExact(const std::uint8_t *data, std::size_t length)
    {
        while(length > 0)
        {
#ifdef _WIN32
            if(!stdin_write_)
                return false;
            DWORD written = 0;
            const DWORD chunk = static_cast<DWORD>(std::min<std::size_t>(length, std::numeric_limits<DWORD>::max()));
            if(!WriteFile(stdin_write_, data, chunk, &written, nullptr) || written == 0)
                return false;
#else
            if(stdin_write_ < 0)
                return false;
            const ssize_t written = write(stdin_write_, data, length);
            if(written <= 0)
                return false;
#endif
            data += written;
            length -= static_cast<std::size_t>(written);
        }
        return true;
    }

    bool readExact(std::uint8_t *data, std::size_t length,
                   std::chrono::steady_clock::time_point deadline)
    {
        while(length > 0)
        {
            const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now());
            if(remaining <= std::chrono::milliseconds::zero())
                return false;
#ifdef _WIN32
            if(!stdout_read_)
                return false;
            DWORD available = 0;
            if(!PeekNamedPipe(stdout_read_, nullptr, 0, nullptr, &available, nullptr))
                return false;
            if(available == 0)
            {
                if(process_ && WaitForSingleObject(process_, 0) != WAIT_TIMEOUT)
                    return false;
                Sleep(10);
                continue;
            }
            DWORD read_count = 0;
            const DWORD chunk = static_cast<DWORD>(std::min<std::size_t>({length, available, std::numeric_limits<DWORD>::max()}));
            if(!ReadFile(stdout_read_, data, chunk, &read_count, nullptr) || read_count == 0)
                return false;
#else
            if(stdout_read_ < 0)
                return false;
            pollfd descriptor {stdout_read_, POLLIN, 0};
            const int poll_timeout = static_cast<int>(std::min<std::int64_t>(remaining.count(), std::numeric_limits<int>::max()));
            if(poll(&descriptor, 1, poll_timeout) <= 0 || !(descriptor.revents & (POLLIN | POLLHUP)))
                return false;
            const ssize_t read_count = read(stdout_read_, data, length);
            if(read_count <= 0)
                return false;
#endif
            data += read_count;
            length -= static_cast<std::size_t>(read_count);
        }
        return true;
    }

#ifdef _WIN32
    HANDLE stdin_write_ = nullptr;
    HANDLE stdout_read_ = nullptr;
    HANDLE process_ = nullptr;
#else
    int stdin_write_ = -1;
    int stdout_read_ = -1;
    pid_t child_pid_ = -1;
#endif
};

class MihomoFetchClient
{
public:
    int fetch(const FetchArgument &argument, FetchResult &result)
    {
        std::lock_guard<std::mutex> guard(io_mutex_);
        if(!ensureStarted())
            return fail(result, "Mihomo subscription transport is unavailable");

        json headers = json::object();
        if(argument.request_headers)
        {
            for(const auto &[name, value] : *argument.request_headers)
                headers[name] = json::array({value});
        }

        json request = {
            {"type", "fetch"},
            {"url", argument.url},
            {"headers", std::move(headers)},
            {"proxy", argument.proxy},
            {"old_hash", argument.old_hash ? *argument.old_hash : std::string()},
            {"timeout_ms", 20000},
            {"size_limit", global.maxAllowedDownloadSize},
        };
        for(int attempt = 1; attempt <= max_fetch_attempts; ++attempt)
        {
            const std::uint64_t request_id = next_request_id_++;
            request["id"] = request_id;
            const auto payload = json::to_cbor(request);
            if(!process_.writeFrame(payload))
            {
                invalidate();
                return fail(result, "Mihomo subscription transport write failed");
            }

            const auto timeout = std::chrono::milliseconds(25000);
            auto response_frame = process_.readFrame(timeout);
            if(!response_frame)
            {
                invalidate();
                return fail(result, "Mihomo subscription transport timed out");
            }

            try
            {
                const json response = json::from_cbor(*response_frame, true, true);
                if(response.value("type", "") != "response" || response.value("id", std::uint64_t{0}) != request_id)
                {
                    invalidate();
                    return fail(result, "Mihomo subscription transport protocol mismatch");
                }
                const int status = response.value("status", 0);
                *result.status_code = status;
                if(result.content)
                {
                    result.content->clear();
                    if(response.contains("body") && response["body"].is_binary())
                    {
                        const auto &body = response["body"].get_binary();
                        result.content->assign(reinterpret_cast<const char *>(body.data()), body.size());
                    }
                }
                if(result.response_headers)
                {
                    result.response_headers->clear();
                    if(response.contains("headers") && response["headers"].is_object())
                    {
                        for(const auto &[name, values] : response["headers"].items())
                        {
                            if(!values.is_array())
                                continue;
                            for(const auto &value : values)
                            {
                                if(value.is_string())
                                    *result.response_headers += name + ": " + value.get<std::string>() + "\r\n";
                            }
                        }
                    }
                }
                if(result.body_hash)
                {
                    result.body_hash->clear();
                    if(response.contains("body_hash") && response["body_hash"].is_string())
                        *result.body_hash = response["body_hash"].get<std::string>();
                }

                const std::string error_code = response.value("error_code", "");
                if(error_code.empty())
                    return status;
                if(attempt < max_fetch_attempts && isTransientFetchError(error_code))
                {
                    invalidate();
                    if(!ensureStarted())
                        return fail(result, "Mihomo subscription transport is unavailable");
                    continue;
                }
                return fail(result, "Mihomo subscription request failed", status);
            }
            catch(const std::exception &)
            {
                invalidate();
                return fail(result, "Mihomo subscription transport returned invalid data");
            }
        }
        return fail(result, "Mihomo subscription request failed");
    }

private:
    bool ensureStarted()
    {
        if(started_)
            return true;
        const auto helper = locateHelper();
        if(!helper)
            return false;
        const auto manifest = locateManifest(*helper);
        if(!manifest || !validateHelperIdentity(*helper, *manifest) || !process_.start(*helper))
            return false;
        auto hello_frame = process_.readFrame(std::chrono::duration_cast<std::chrono::milliseconds>(startup_timeout));
        if(!hello_frame)
        {
            process_.stop();
            return false;
        }
        try
        {
            const json hello = json::from_cbor(*hello_frame, true, true);
            const std::string expected_version = SUBCONVERTER_MIHOMO_VERSION;
            const std::string expected_commit = SUBCONVERTER_MIHOMO_COMMIT;
            const std::string expected_overlay = SUBCONVERTER_MIHOMO_OVERLAY_SHA256;
            const auto [expected_goos, expected_goarch] = runtimeGoTarget();
            const auto go_version = hello.find("go_version");
            const bool identity_matches =
                hello.is_object() &&
                hello.value("type", "") == "hello" &&
                hello.value("protocol", std::uint64_t{0}) == protocol_version &&
                !expected_version.empty() && hello.value("mihomo_version", "") == expected_version &&
                !expected_commit.empty() && hello.value("mihomo_commit", "") == expected_commit &&
                !expected_overlay.empty() && hello.value("overlay_sha256", "") == expected_overlay &&
                !expected_goos.empty() && hello.value("goos", "") == expected_goos &&
                !expected_goarch.empty() && hello.value("goarch", "") == expected_goarch &&
                go_version != hello.end() && go_version->is_string() && !go_version->get_ref<const std::string &>().empty() &&
                hello.value("default_user_agent", "") == "clash.meta/" + expected_version &&
                hello.contains("capabilities") && exactCapabilities(hello["capabilities"]);
            if(!identity_matches)
            {
                process_.stop();
                return false;
            }
        }
        catch(const std::exception &)
        {
            process_.stop();
            return false;
        }
        started_ = true;
        return true;
    }

    int fail(FetchResult &result, const std::string &message, int status = 0)
    {
        *result.status_code = status;
        if(result.content)
            result.content->clear();
        if(result.response_headers)
            result.response_headers->clear();
        if(result.body_hash)
            result.body_hash->clear();
        writeLog(0, message, LOG_LEVEL_ERROR);
        return status;
    }

    void invalidate()
    {
        started_ = false;
        process_.stop();
    }

    std::mutex io_mutex_;
    HelperProcess process_;
    std::uint64_t next_request_id_ = 1;
    bool started_ = false;
};

MihomoFetchClient &client()
{
    static MihomoFetchClient instance;
    return instance;
}
}

int mihomoFetch(const FetchArgument &argument, FetchResult &result)
{
    return client().fetch(argument, result);
}
