#include <iostream>
#include <unistd.h>
#include <sys/stat.h>
//#include <mutex>
#include <thread>
#include <atomic>

#include <curl/curl.h>

#include "handler/settings.h"
#include "utils/base64/base64.h"
#include "utils/defer.h"
#include "utils/file_extra.h"
#include "utils/lock.h"
#include "utils/logger.h"
#include "utils/urlencode.h"
#include "version.h"
#include "webget.h"

#ifdef _WIN32
#ifndef _stat
#define _stat stat
#endif // _stat
#endif // _WIN32

/*
using guarded_mutex = std::lock_guard<std::mutex>;
std::mutex cache_rw_lock;
*/

RWLock cache_rw_lock;



struct curl_progress_data
{
    long size_limit = 0L;
};

static inline void curl_init()
{
    static bool init = false;
    if(!init)
    {
        curl_global_init(CURL_GLOBAL_ALL);
        init = true;
    }
}

static int writer(char *data, size_t size, size_t nmemb, std::string *writerData)
{
    if(writerData == nullptr)
        return 0;

    writerData->append(data, size*nmemb);

    return static_cast<int>(size * nmemb);
}

static int dummy_writer(char *, size_t size, size_t nmemb, void *)
{
    /// dummy writer, do not save anything
    return static_cast<int>(size * nmemb);
}

//static int size_checker(void *clientp, curl_off_t dltotal, curl_off_t dlnow, curl_off_t ultotal, curl_off_t ulnow)
static int size_checker(void *clientp, curl_off_t, curl_off_t dlnow, curl_off_t, curl_off_t)
{
    if(clientp)
    {
        auto *data = reinterpret_cast<curl_progress_data*>(clientp);
        if(data->size_limit)
        {
            if(dlnow > data->size_limit)
                return 1;
        }
    }
    return 0;
}

static int logger(CURL *handle, curl_infotype type, char *data, size_t size, void *userptr)
{
    (void)handle;
    (void)userptr;
    std::string prefix;
    switch(type)
    {
    case CURLINFO_TEXT:
        prefix = "CURL_INFO: ";
        break;
    case CURLINFO_HEADER_IN:
        prefix = "CURL_HEADER: < ";
        break;
    case CURLINFO_HEADER_OUT:
        prefix = "CURL_HEADER: > ";
        break;
    case CURLINFO_DATA_IN:
    case CURLINFO_DATA_OUT:
    default:
        return 0;
    }
    std::string content(data, size);
    if(content.find("\r\n") != std::string::npos)
    {
        string_array lines = split(content, "\r\n");
        for(auto &x : lines)
        {
            std::string log_content = prefix;
            log_content += x;
            writeLog(0, log_content, LOG_LEVEL_VERBOSE);
        }
    }
    else
    {
        std::string log_content = prefix;
        log_content += trimWhitespace(content);
        writeLog(0, log_content, LOG_LEVEL_VERBOSE);
    }
    return 0;
}

static inline void curl_set_common_options(CURL *curl_handle, const char *url, curl_progress_data *data)
{
    curl_easy_setopt(curl_handle, CURLOPT_URL, url);
    curl_easy_setopt(curl_handle, CURLOPT_VERBOSE, global.logLevel == LOG_LEVEL_VERBOSE ? 1L : 0L);
    curl_easy_setopt(curl_handle, CURLOPT_DEBUGFUNCTION, logger);
    curl_easy_setopt(curl_handle, CURLOPT_NOPROGRESS, 0L);
    curl_easy_setopt(curl_handle, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(curl_handle, CURLOPT_FOLLOWLOCATION, 1L);
    curl_easy_setopt(curl_handle, CURLOPT_MAXREDIRS, 20L);
    curl_easy_setopt(curl_handle, CURLOPT_SSL_VERIFYPEER, 0L);
    curl_easy_setopt(curl_handle, CURLOPT_SSL_VERIFYHOST, 0L);
    curl_easy_setopt(curl_handle, CURLOPT_TIMEOUT, 15L);
    curl_easy_setopt(curl_handle, CURLOPT_COOKIEFILE, "");
    if(data)
    {
        if(data->size_limit)
            curl_easy_setopt(curl_handle, CURLOPT_MAXFILESIZE, data->size_limit);
        curl_easy_setopt(curl_handle, CURLOPT_XFERINFOFUNCTION, size_checker);
        curl_easy_setopt(curl_handle, CURLOPT_XFERINFODATA, data);
    }
}

//static std::string curlGet(const std::string &url, const std::string &proxy, std::string &response_headers, CURLcode &return_code, const string_map &request_headers)
static int curlGet(const FetchArgument &argument, FetchResult &result)
{
    CURL *curl_handle;
    std::string *data = result.content, new_url = argument.url;
    curl_slist *header_list = nullptr;
    defer(curl_slist_free_all(header_list);)
    long retVal;

    curl_init();

    curl_handle = curl_easy_init();
    if(!argument.proxy.empty())
    {
        if(startsWith(argument.proxy, "cors:"))
        {
            header_list = curl_slist_append(header_list, "X-Requested-With: subconverter " VERSION);
            new_url = argument.proxy.substr(5) + argument.url;
        }
        else
            curl_easy_setopt(curl_handle, CURLOPT_PROXY, argument.proxy.data());
    }
    curl_progress_data limit;
    limit.size_limit = global.maxAllowedDownloadSize;
    curl_set_common_options(curl_handle, new_url.data(), &limit);
    header_list = curl_slist_append(header_list, "Content-Type: application/json;charset=utf-8");
    
    // 传递用户的UA给机场
    std::string final_ua = "clash.meta"; // 默认使用clash.meta UA
    if (argument.request_headers) {
        auto ua_iter = argument.request_headers->find("User-Agent");
        if (ua_iter != argument.request_headers->end() && !ua_iter->second.empty()) {
            final_ua = ua_iter->second;
        }
    }
    curl_easy_setopt(curl_handle, CURLOPT_USERAGENT, final_ua.c_str());
    
    // 使用系统服务标识来防止循环请求，同时隐藏subconverter特征
    header_list = curl_slist_append(header_list, "X-Service-ID: config-processor");
    // 完全移除版本头部，避免向机场暴露任何subconverter特征
    if(header_list)
        curl_easy_setopt(curl_handle, CURLOPT_HTTPHEADER, header_list);

    if(result.content)
    {
        curl_easy_setopt(curl_handle, CURLOPT_WRITEFUNCTION, writer);
        curl_easy_setopt(curl_handle, CURLOPT_WRITEDATA, result.content);
    }
    else
        curl_easy_setopt(curl_handle, CURLOPT_WRITEFUNCTION, dummy_writer);
    if(result.response_headers)
    {
        curl_easy_setopt(curl_handle, CURLOPT_HEADERFUNCTION, writer);
        curl_easy_setopt(curl_handle, CURLOPT_HEADERDATA, result.response_headers);
    }
    else
        curl_easy_setopt(curl_handle, CURLOPT_HEADERFUNCTION, dummy_writer);

    if(argument.cookies)
    {
        string_array cookies = split(*argument.cookies, "\r\n");
        for(auto &x : cookies)
            curl_easy_setopt(curl_handle, CURLOPT_COOKIELIST, x.c_str());
    }

    switch(argument.method)
    {
    case HTTP_POST:
        curl_easy_setopt(curl_handle, CURLOPT_POST, 1L);
        if(argument.post_data)
        {
            curl_easy_setopt(curl_handle, CURLOPT_POSTFIELDS, argument.post_data->data());
            curl_easy_setopt(curl_handle, CURLOPT_POSTFIELDSIZE, argument.post_data->size());
        }
        break;
    case HTTP_PATCH:
        curl_easy_setopt(curl_handle, CURLOPT_CUSTOMREQUEST, "PATCH");
        if(argument.post_data)
        {
            curl_easy_setopt(curl_handle, CURLOPT_POSTFIELDS, argument.post_data->data());
            curl_easy_setopt(curl_handle, CURLOPT_POSTFIELDSIZE, argument.post_data->size());
        }
        break;
    case HTTP_HEAD:
        curl_easy_setopt(curl_handle, CURLOPT_NOBODY, 1L);
        break;
    case HTTP_GET:
        break;
    }

    unsigned int fail_count = 0, max_fails = 1;
    while(true)
    {
        retVal = curl_easy_perform(curl_handle);
        if(retVal == CURLE_OK || max_fails <= fail_count || global.APIMode)
            break;
        else
            fail_count++;
    }

    long code = 0;
    curl_easy_getinfo(curl_handle, CURLINFO_HTTP_CODE, &code);
    *result.status_code = code;

    if(result.cookies)
    {
        curl_slist *cookies = nullptr;
        curl_easy_getinfo(curl_handle, CURLINFO_COOKIELIST, &cookies);
        if(cookies)
        {
            auto each = cookies;
            while(each)
            {
                result.cookies->append(each->data);
                *result.cookies += "\r\n";
                each = each->next;
            }
        }
        curl_slist_free_all(cookies);
    }

    curl_easy_cleanup(curl_handle);

    if(data && !argument.keep_resp_on_fail)
    {
        if(retVal != CURLE_OK || *result.status_code != 200)
            data->clear();
        data->shrink_to_fit();
    }

    return *result.status_code;
}

// data:[<mediatype>][;base64],<data>
static std::string dataGet(const std::string &url)
{
    if (!startsWith(url, "data:"))
        return "";
    std::string::size_type comma = url.find(',');
    if (comma == std::string::npos || comma == url.size() - 1)
        return "";

    std::string data = urlDecode(url.substr(comma + 1));
    if (endsWith(url.substr(0, comma), ";base64")) {
        return urlSafeBase64Decode(data);
    } else {
        return data;
    }
}

std::string buildSocks5ProxyString(const std::string &addr, int port, const std::string &username, const std::string &password)
{
    std::string authstr = username.size() && password.size() ? username + ":" + password + "@" : "";
    std::string proxystr = "socks5://" + authstr + addr + ":" + std::to_string(port);
    return proxystr;
}

std::string webGet(const std::string &url, const std::string &proxy, unsigned int cache_ttl, std::string *response_headers, string_icase_map *request_headers)
{
    int return_code = 0;
    std::string content;

    FetchArgument argument {HTTP_GET, url, proxy, nullptr, request_headers, nullptr, cache_ttl};
    FetchResult fetch_res {&return_code, &content, response_headers, nullptr};

    if (startsWith(url, "data:"))
        return dataGet(url);
    // cache system
    if(cache_ttl > 0)
    {
        md("cache");
        // 由于不再传递用户头部，缓存键只依赖URL
        // 这样简化了缓存逻辑，所有相同URL的请求都使用相同缓存
        std::string cache_key = url;
        const std::string url_md5 = getMD5(cache_key);
        const std::string path = "cache/" + url_md5, path_header = path + "_header";
        struct stat result {};
        if(stat(path.data(), &result) == 0) // cache exist
        {
            time_t mtime = result.st_mtime, now = time(nullptr); // get cache modified time and current time
            if(difftime(now, mtime) <= cache_ttl) // within TTL
            {
                writeLog(0, "CACHE HIT: '" + url + "', using local cache.");
                //guarded_mutex guard(cache_rw_lock);
                cache_rw_lock.readLock();
                defer(cache_rw_lock.readUnlock();)
                if(response_headers)
                    *response_headers = fileGet(path_header, true);
                return fileGet(path, true);
            }
            writeLog(0, "CACHE MISS: '" + url + "', TTL timeout, creating new cache."); // out of TTL
        }
        else
            writeLog(0, "CACHE NOT EXIST: '" + url + "', creating new cache.");
        //content = curlGet(url, proxy, response_headers, return_code); // try to fetch data
        curlGet(argument, fetch_res);
        if(return_code == 200) // success, save new cache
        {
            //guarded_mutex guard(cache_rw_lock);
            cache_rw_lock.writeLock();
            defer(cache_rw_lock.writeUnlock();)
            fileWrite(path, content, true);
            if(response_headers)
                fileWrite(path_header, *response_headers, true);
        }
        else
        {
            if(fileExist(path) && global.serveCacheOnFetchFail) // failed, check if cache exist
            {
                writeLog(0, "Fetch failed. Serving cached content."); // cache exist, serving cache
                //guarded_mutex guard(cache_rw_lock);
                cache_rw_lock.readLock();
                defer(cache_rw_lock.readUnlock();)
                content = fileGet(path, true);
                if(response_headers)
                    *response_headers = fileGet(path_header, true);
            }
            else
                writeLog(0, "Fetch failed. No local cache available."); // cache not exist or not allow to serve cache, serving nothing
        }
        return content;
    }
    //return curlGet(url, proxy, response_headers, return_code);
    curlGet(argument, fetch_res);
    return content;
}

void flushCache()
{
    //guarded_mutex guard(cache_rw_lock);
    cache_rw_lock.writeLock();
    defer(cache_rw_lock.writeUnlock();)
    operateFiles("cache", [](const std::string &file){ remove(("cache/" + file).data()); return 0; });
}

int webPost(const std::string &url, const std::string &data, const std::string &proxy, const string_icase_map &request_headers, std::string *retData)
{
    //return curlPost(url, data, proxy, request_headers, retData);
    int return_code = 0;
    FetchArgument argument {HTTP_POST, url, proxy, &data, &request_headers, nullptr, 0, true};
    FetchResult fetch_res {&return_code, retData, nullptr, nullptr};
    return webGet(argument, fetch_res);
}

int webPatch(const std::string &url, const std::string &data, const std::string &proxy, const string_icase_map &request_headers, std::string *retData)
{
    //return curlPatch(url, data, proxy, request_headers, retData);
    int return_code = 0;
    FetchArgument argument {HTTP_PATCH, url, proxy, &data, &request_headers, nullptr, 0, true};
    FetchResult fetch_res {&return_code, retData, nullptr, nullptr};
    return webGet(argument, fetch_res);
}

int webHead(const std::string &url, const std::string &proxy, const string_icase_map &request_headers, std::string &response_headers)
{
    //return curlHead(url, proxy, request_headers, response_headers);
    int return_code = 0;
    FetchArgument argument {HTTP_HEAD, url, proxy, nullptr, &request_headers, nullptr, 0};
    FetchResult fetch_res {&return_code, nullptr, &response_headers, nullptr};
    return webGet(argument, fetch_res);
}

string_array headers_map_to_array(const string_map &headers)
{
    string_array result;
    for(auto &kv : headers)
        result.push_back(kv.first + ": " + kv.second);
    return result;
}

int webGet(const FetchArgument& argument, FetchResult &result)
{
    return curlGet(argument, result);
}
