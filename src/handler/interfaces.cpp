#include <iostream>
#include <string>
#include <mutex>
#include <numeric>
#include <algorithm>

#include <yaml-cpp/yaml.h>

#include "config/binding.h"
#include "generator/config/nodemanip.h"
#include "generator/config/ruleconvert.h"
#include "generator/config/subexport.h"
#include "generator/template/templates.h"
#include "script/script_quickjs.h"
#include "server/webserver.h"
#include "utils/base64/base64.h"
#include "utils/file_extra.h"
#include "utils/ini_reader/ini_reader.h"
#include "utils/logger.h"
#include "utils/network.h"
#include "utils/regexp.h"
#include "utils/stl_extra.h"
#include "utils/string.h"
#include "utils/string_hash.h"
#include "utils/system.h"
#include "utils/urlencode.h"
#include "interfaces.h"
#include "multithread.h"
#include "settings.h"
#include "upload.h"
#include "webget.h"

extern WebServer webServer;

string_array gRegexBlacklist = {"(.*)*"};

std::string parseProxy(const std::string &source)
{
    std::string proxy = source;
    if(source == "SYSTEM")
        proxy = getSystemProxy();
    else if(source == "NONE")
        proxy = "";
    return proxy;
}

extern string_array ClashRuleTypes, SurgeRuleTypes, QuanXRuleTypes;

struct UAProfile
{
    std::string head;
    std::string version_match;
    std::string version_target;
    std::string target;
    tribool clash_new_name = tribool();
    int surge_ver = -1;
};

const std::vector<UAProfile> UAMatchList = {
    {"ClashForAndroid","\\/([0-9.]+)","2.0","clash",true},
    {"ClashForAndroid","\\/([0-9.]+)R","","clashr",false},
    {"ClashForAndroid","","","clash",false},
    {"ClashforWindows","\\/([0-9.]+)","0.11","clash",true},
    {"ClashforWindows","","","clash",false},
    {"clash-verge","","","clash",true},
    {"ClashX Pro","","","clash",true},
    {"ClashX","\\/([0-9.]+)","0.13","clash",true},
    {"Clash","","","clash",true},
    {"clash.meta","","","clash",true}, /// 添加 clash.meta 支持新字段名
    {"Kitsunebi","","","v2ray"},
    {"Loon","","","loon"},
    {"Pharos","","","mixed"},
    {"Potatso","","","mixed"},
    {"Quantumult%20X","","","quanx"},
    {"Quantumult","","","quan"},
    {"Qv2ray","","","v2ray"},
    {"Shadowrocket","","","mixed"},
    {"Surfboard","","","surfboard"},
    {"Surge","\\/([0-9.]+).*x86","906","surge",false,4}, /// Surge for Mac (supports VMess)
    {"Surge","\\/([0-9.]+).*x86","368","surge",false,3}, /// Surge for Mac (supports new rule types and Shadowsocks without plugin)
    {"Surge","\\/([0-9.]+)","1419","surge",false,4}, /// Surge iOS 4 (first version)
    {"Surge","\\/([0-9.]+)","900","surge",false,3}, /// Surge iOS 3 (approx)
    {"Surge","","","surge",false,2}, /// any version of Surge as fallback
    {"Trojan-Qt5","","","trojan"},
    {"V2rayU","","","v2ray"},
    {"V2RayX","","","v2ray"}
};

bool verGreaterEqual(const std::string& src_ver, const std::string& target_ver)
{
    std::istringstream src_stream(src_ver), target_stream(target_ver);
    int src_part, target_part;
    char dot;
    while (src_stream >> src_part) {
        if (target_stream >> target_part) {
            if (src_part < target_part) {
                return false;
            } else if (src_part > target_part) {
                return true;
            }
            // Skip the dot separator in both streams
            src_stream >> dot;
            target_stream >> dot;
        } else {
            // If we run out of target parts, the source version is greater only if it has more parts
            return true;
        }
    }
    // If we get here, the common parts are equal, so check if target_ver has more parts
    return !bool(target_stream >> target_part);
}

void matchUserAgent(const std::string &user_agent, std::string &target, tribool &clash_new_name, int &surge_ver)
{
    if(user_agent.empty())
        return;
    
    // 100%传递用户的UA，不再进行任何特殊处理
    // 所有UA都按原样处理，确保完全传递用户意图
    for(const UAProfile &x : UAMatchList)
    {
        if(startsWith(user_agent, x.head))
        {
            if(!x.version_match.empty())
            {
                std::string version;
                if(regGetMatch(user_agent, x.version_match, 2, 0, &version))
                    continue;
                if(!x.version_target.empty() && !verGreaterEqual(version, x.version_target))
                    continue;
            }
            target = x.target;
            clash_new_name = x.clash_new_name;
            if(x.surge_ver != -1)
                surge_ver = x.surge_ver;
            return;
        }
    }
}

std::string getRuleset(RESPONSE_CALLBACK_ARGS)
{
    auto &argument = request.argument;
    int *status_code = &response.status_code;
    /// type: 1 for Surge, 2 for Quantumult X, 3 for Clash domain rule-provider, 4 for Clash ipcidr rule-provider, 5 for Surge DOMAIN-SET, 6 for Clash classical ruleset
    std::string url = urlSafeBase64Decode(getUrlArg(argument, "url")), type = getUrlArg(argument, "type"), group = urlSafeBase64Decode(getUrlArg(argument, "group"));
    std::string output_content, dummy;
    int type_int = to_int(type, 0);

    if(url.empty() || type.empty() || (type_int == 2 && group.empty()) || (type_int < 1 || type_int > 6))
    {
        *status_code = 400;
        return "Invalid request!";
    }

    std::string proxy = parseProxy(global.proxyRuleset);
    string_array vArray = split(url, "|");
    for(std::string &x : vArray)
        x.insert(0, "ruleset,");
    std::vector<RulesetContent> rca;
    RulesetConfigs confs = INIBinding::from<RulesetConfig>::from_ini(vArray);
    refreshRulesets(confs, rca);
    for(RulesetContent &x : rca)
    {
        std::string content = x.rule_content.get();
        output_content += convertRuleset(content, x.rule_type);
    }

    if(output_content.empty())
    {
        *status_code = 400;
        return "Invalid request!";
    }

    std::string strLine;
    std::stringstream ss;
    const std::string rule_match_regex = "^(.*?,.*?)(,.*)(,.*)$";

    ss << output_content;
    char delimiter = getLineBreak(output_content);
    std::string::size_type lineSize, posb, pose;
    auto filterLine = [&]()
    {
        posb = 0;
        pose = strLine.find(',');
        if(pose == std::string::npos)
            return 1;
        posb = pose + 1;
        pose = strLine.find(',', posb);
        if(pose == std::string::npos)
        {
            pose = strLine.size();
            if(strLine[pose - 1] == '\r')
                pose--;
        }
        pose -= posb;
        return 0;
    };

    lineSize = output_content.size();
    output_content.clear();
    output_content.reserve(lineSize);

    if(type_int == 3 || type_int == 4 || type_int == 6)
        output_content = "payload:\n";

    while(getline(ss, strLine, delimiter))
    {
        if(strFind(strLine, "//"))
        {
            strLine.erase(strLine.find("//"));
            strLine = trimWhitespace(strLine);
        }
        switch(type_int)
        {
        case 2:
            if(!std::any_of(QuanXRuleTypes.begin(), QuanXRuleTypes.end(), [&strLine](const std::string& type){return startsWith(strLine, type);}))
                continue;
            break;
        case 1:
            if(!std::any_of(SurgeRuleTypes.begin(), SurgeRuleTypes.end(), [&strLine](const std::string& type){return startsWith(strLine, type);}))
                continue;
            break;
        case 3:
            if(!startsWith(strLine, "DOMAIN-SUFFIX,") && !startsWith(strLine, "DOMAIN,"))
                continue;
            if(filterLine())
                continue;
            output_content += "  - '";
            if(strLine[posb - 2] == 'X')
                output_content += "+.";
            output_content += trim(strLine.substr(posb, pose));
            output_content += "'\n";
            continue;
        case 4:
            if(!startsWith(strLine, "IP-CIDR,") && !startsWith(strLine, "IP-CIDR6,"))
                continue;
            if(filterLine())
                continue;
            output_content += "  - '";
            output_content += trim(strLine.substr(posb, pose));
            output_content += "'\n";
            continue;
        case 5:
            if(!startsWith(strLine, "DOMAIN-SUFFIX,") && !startsWith(strLine, "DOMAIN,"))
                continue;
            if(filterLine())
                continue;
            if(strLine[posb - 2] == 'X')
                output_content += '.';
            output_content += trim(strLine.substr(posb, pose));
            output_content += '\n';
            continue;
        case 6:
            if(!std::any_of(ClashRuleTypes.begin(), ClashRuleTypes.end(), [&strLine](const std::string& type){return startsWith(strLine, type);}))
                continue;
            output_content += "  - ";
        default:
            break;
        }

        lineSize = strLine.size();
        if(lineSize && strLine[lineSize - 1] == '\r') //remove line break
            strLine.erase(--lineSize);

        if(!strLine.empty() && (strLine[0] != ';' && strLine[0] != '#' && !(lineSize >= 2 && strLine[0] == '/' && strLine[1] == '/')))
        {
            if(type_int == 2)
            {
                if(startsWith(strLine, "IP-CIDR6"))
                    strLine.replace(0, 8, "IP6-CIDR");
                strLine += "," + group;
                if(count_least(strLine, ',', 3) && regReplace(strLine, rule_match_regex, "$2") == ",no-resolve")
                    strLine = regReplace(strLine, rule_match_regex, "$1$3$2");
                else
                    strLine = regReplace(strLine, rule_match_regex, "$1$3");
            }
        }
        output_content += strLine;
        output_content += '\n';
    }

    if(output_content == "payload:\n")
    {
        switch(type_int)
        {
        case 3:
            output_content += "  - '--placeholder--'";
            break;
        case 4:
            output_content += "  - '0.0.0.0/32'";
            break;
        case 6:
            output_content += "  - 'DOMAIN,--placeholder--'";
            break;
        }
    }
    return output_content;
}

void checkExternalBase(const std::string &path, std::string &dest)
{
    if(isLink(path) || (startsWith(path, global.basePath) && fileExist(path)))
        dest = path;
}

std::string subconverter(RESPONSE_CALLBACK_ARGS)
{
    auto &argument = request.argument;
    int *status_code = &response.status_code;

    std::string argTarget = getUrlArg(argument, "target"), argSurgeVer = getUrlArg(argument, "ver");
    tribool argClashNewField = getUrlArg(argument, "new_name");
    int intSurgeVer = !argSurgeVer.empty() ? to_int(argSurgeVer, 3) : 3;
    // 完全禁用UA识别，所有请求都按浏览器方式处理，避免客户端特定逻辑
    // if(argTarget == "auto")
    //     matchUserAgent(request.headers["User-Agent"], argTarget, argClashNewField, intSurgeVer);

    /// don't try to load groups or rulesets when generating simple subscriptions
    bool lSimpleSubscription = false;
    switch(hash_(argTarget))
    {
    case "ss"_hash: case "ssd"_hash: case "ssr"_hash: case "sssub"_hash: case "v2ray"_hash: case "trojan"_hash: case "mixed"_hash:
        lSimpleSubscription = true;
        break;
    case "clash"_hash: case "clashr"_hash: case "surge"_hash: case "quan"_hash: case "quanx"_hash: case "loon"_hash: case "surfboard"_hash: case "mellow"_hash: case "singbox"_hash:
        break;
    default:
        *status_code = 400;
        return "Invalid target!";
    }
    //check if we need to read configuration
    if(global.reloadConfOnRequest && (!global.APIMode || global.CFWChildProcess) && !global.generatorMode)
        readConf();

    /// string values
    std::string argUrl = getUrlArg(argument, "url");
    std::string argGroupName = getUrlArg(argument, "group"), argUploadPath = getUrlArg(argument, "upload_path");
    std::string argIncludeRemark = getUrlArg(argument, "include"), argExcludeRemark = getUrlArg(argument, "exclude");
    std::string argCustomGroups = urlSafeBase64Decode(getUrlArg(argument, "groups")), argCustomRulesets = urlSafeBase64Decode(getUrlArg(argument, "ruleset")), argExternalConfig = getUrlArg(argument, "config");
    std::string argDeviceID = getUrlArg(argument, "dev_id"), argFilename = getUrlArg(argument, "filename"), argUpdateInterval = getUrlArg(argument, "interval"), argUpdateStrict = getUrlArg(argument, "strict");
    std::string argRenames = getUrlArg(argument, "rename"), argFilterScript = getUrlArg(argument, "filter_script");
    std::string argUserAgent = getUrlArg(argument, "ua");

    /// switches with default value
    tribool argUpload = getUrlArg(argument, "upload"), argEmoji = getUrlArg(argument, "emoji"), argAddEmoji = getUrlArg(argument, "add_emoji"), argRemoveEmoji = getUrlArg(argument, "remove_emoji");
    tribool argAppendType = getUrlArg(argument, "append_type"), argTFO = getUrlArg(argument, "tfo"), argUDP = getUrlArg(argument, "udp"), argGenNodeList = getUrlArg(argument, "list");
    tribool argSort = getUrlArg(argument, "sort"), argUseSortScript = getUrlArg(argument, "sort_script");
    tribool argGenClashScript = getUrlArg(argument, "script"), argEnableInsert = getUrlArg(argument, "insert");
    tribool argSkipCertVerify = getUrlArg(argument, "scv"), argFilterDeprecated = getUrlArg(argument, "fdn"), argExpandRulesets = getUrlArg(argument, "expand"), argAppendUserinfo = getUrlArg(argument, "append_info");
    tribool argPrependInsert = getUrlArg(argument, "prepend"), argGenClassicalRuleProvider = getUrlArg(argument, "classic"), argTLS13 = getUrlArg(argument, "tls13");

    std::string base_content, output_content;
    ProxyGroupConfigs lCustomProxyGroups = global.customProxyGroups;
    RulesetConfigs lCustomRulesets = global.customRulesets;
    string_array lIncludeRemarks = global.includeRemarks, lExcludeRemarks = global.excludeRemarks;
    std::vector<RulesetContent> lRulesetContent;
    extra_settings ext;
    std::string subInfo, dummy;
    int interval = !argUpdateInterval.empty() ? to_int(argUpdateInterval, global.updateInterval) : global.updateInterval;
    bool authorized = !global.APIMode || getUrlArg(argument, "token") == global.accessToken, strict = !argUpdateStrict.empty() ? argUpdateStrict == "true" : global.updateStrict;

    if(std::find(gRegexBlacklist.cbegin(), gRegexBlacklist.cend(), argIncludeRemark) != gRegexBlacklist.cend() || std::find(gRegexBlacklist.cbegin(), gRegexBlacklist.cend(), argExcludeRemark) != gRegexBlacklist.cend())
        return "Invalid request!";

    /// for external configuration
    std::string lClashBase = global.clashBase, lSurgeBase = global.surgeBase, lMellowBase = global.mellowBase, lSurfboardBase = global.surfboardBase;
    std::string lQuanBase = global.quanBase, lQuanXBase = global.quanXBase, lLoonBase = global.loonBase, lSSSubBase = global.SSSubBase;
    std::string lSingBoxBase = global.singBoxBase;

    /// validate urls
    argEnableInsert.define(global.enableInsert);
    if(argUrl.empty() && (!global.APIMode || authorized))
        argUrl = global.defaultUrls;
    if((argUrl.empty() && !(!global.insertUrls.empty() && argEnableInsert)) || argTarget.empty())
    {
        *status_code = 400;
        return "Invalid request!";
    }

    /// load request arguments as template variables
//    string_array req_args = split(argument, "&");
//    string_map req_arg_map;
//    for(std::string &x : req_args)
//    {
//        string_size pos = x.find("=");
//        if(pos == x.npos)
//        {
//            req_arg_map[x] = "";
//            continue;
//        }
//        if(x.substr(0, pos) == "token")
//            continue;
//        req_arg_map[x.substr(0, pos)] = x.substr(pos + 1);
//    }
    string_map req_arg_map;
    for (auto &x : argument)
    {
        if(x.first == "token")
            continue;
        req_arg_map[x.first] = x.second;
    }
    req_arg_map["target"] = argTarget;
    req_arg_map["ver"] = std::to_string(intSurgeVer);

    /// save template variables
    template_args tpl_args;
    tpl_args.global_vars = global.templateVars;
    tpl_args.request_params = req_arg_map;

    /// check for proxy settings
    std::string proxy = parseProxy(global.proxySubscription);

    /// check other flags
    ext.authorized = authorized;
    ext.append_proxy_type = argAppendType.get(global.appendType);
    if((argTarget == "clash" || argTarget == "clashr") && argGenClashScript.is_undef())
        argExpandRulesets.define(true);

    ext.clash_proxies_style = global.clashProxiesStyle;
    ext.clash_proxy_groups_style = global.clashProxyGroupsStyle;

    /// read preference from argument, assign global var if not in argument
    ext.tfo.define(argTFO).define(global.TFOFlag);
    ext.udp.define(argUDP).define(global.UDPFlag);
    ext.skip_cert_verify.define(argSkipCertVerify).define(global.skipCertVerify);
    ext.tls13.define(argTLS13).define(global.TLS13Flag);

    ext.sort_flag = argSort.get(global.enableSort);
    argUseSortScript.define(!global.sortScript.empty());
    if(ext.sort_flag && argUseSortScript)
        ext.sort_script = global.sortScript;
    ext.filter_deprecated = argFilterDeprecated.get(global.filterDeprecated);
    ext.clash_new_field_name = argClashNewField.get(global.clashUseNewField);
    ext.clash_script = argGenClashScript.get();
    ext.clash_classical_ruleset = argGenClassicalRuleProvider.get();
    if(!argExpandRulesets)
        ext.clash_new_field_name = true;
    else
        ext.clash_script = false;

    ext.nodelist = argGenNodeList;
    ext.surge_ssr_path = global.surgeSSRPath;
    ext.quanx_dev_id = !argDeviceID.empty() ? argDeviceID : global.quanXDevID;
    ext.enable_rule_generator = global.enableRuleGen;
    ext.overwrite_original_rules = global.overwriteOriginalRules;
    if(!argExpandRulesets)
        ext.managed_config_prefix = global.managedConfigPrefix;

    /// load external configuration
    if(argExternalConfig.empty())
        argExternalConfig = global.defaultExtConfig;
    if(!argExternalConfig.empty())
    {
        //std::cerr<<"External configuration file provided. Loading...\n";
        writeLog(0, "External configuration file provided. Loading...", LOG_LEVEL_INFO);
        ExternalConfig extconf;
        extconf.tpl_args = &tpl_args;
        if(loadExternalConfig(argExternalConfig, extconf) == 0)
        {
            if(!ext.nodelist)
            {
                checkExternalBase(extconf.sssub_rule_base, lSSSubBase);
                if(!lSimpleSubscription)
                {
                    checkExternalBase(extconf.clash_rule_base, lClashBase);
                    checkExternalBase(extconf.surge_rule_base, lSurgeBase);
                    checkExternalBase(extconf.surfboard_rule_base, lSurfboardBase);
                    checkExternalBase(extconf.mellow_rule_base, lMellowBase);
                    checkExternalBase(extconf.quan_rule_base, lQuanBase);
                    checkExternalBase(extconf.quanx_rule_base, lQuanXBase);
                    checkExternalBase(extconf.loon_rule_base, lLoonBase);
                    checkExternalBase(extconf.singbox_rule_base, lSingBoxBase);

                    if(!extconf.surge_ruleset.empty())
                        lCustomRulesets = extconf.surge_ruleset;
                    if(!extconf.custom_proxy_group.empty())
                        lCustomProxyGroups = extconf.custom_proxy_group;
                    ext.enable_rule_generator = extconf.enable_rule_generator;
                    ext.overwrite_original_rules = extconf.overwrite_original_rules;
                }
            }
            if(!extconf.rename.empty())
                ext.rename_array = extconf.rename;
            if(!extconf.emoji.empty())
                ext.emoji_array = extconf.emoji;
            if(!extconf.include.empty())
                lIncludeRemarks = extconf.include;
            if(!extconf.exclude.empty())
                lExcludeRemarks = extconf.exclude;
            argAddEmoji.define(extconf.add_emoji);
            argRemoveEmoji.define(extconf.remove_old_emoji);
        }
    }
    else
    {
        if(!lSimpleSubscription)
        {
            /// loading custom groups
            if(!argCustomGroups.empty() && !ext.nodelist)
            {
                string_array vArray = split(argCustomGroups, "@");
                lCustomProxyGroups = INIBinding::from<ProxyGroupConfig>::from_ini(vArray);
            }

            /// loading custom rulesets
            if(!argCustomRulesets.empty() && !ext.nodelist)
            {
                string_array vArray = split(argCustomRulesets, "@");
                lCustomRulesets = INIBinding::from<RulesetConfig>::from_ini(vArray);
            }
        }
    }
    if(ext.enable_rule_generator && !ext.nodelist && !lSimpleSubscription)
    {
        if(lCustomRulesets != global.customRulesets)
            refreshRulesets(lCustomRulesets, lRulesetContent);
        else
        {
            if(global.updateRulesetOnRequest)
                refreshRulesets(global.customRulesets, global.rulesetsContent);
            lRulesetContent = global.rulesetsContent;
        }
    }

    if(!argEmoji.is_undef())
    {
        argAddEmoji.set(argEmoji);
        argRemoveEmoji.set(true);
    }
    ext.add_emoji = argAddEmoji.get(global.addEmoji);
    ext.remove_emoji = argRemoveEmoji.get(global.removeEmoji);
    if(ext.add_emoji && ext.emoji_array.empty())
        ext.emoji_array = safe_get_emojis();
    if(!argRenames.empty())
        ext.rename_array = INIBinding::from<RegexMatchConfig>::from_ini(split(argRenames, "`"), "@");
    else if(ext.rename_array.empty())
        ext.rename_array = safe_get_renames();

    /// check custom include/exclude settings
    if (!argIncludeRemark.empty())
    {
        // Check if the delimiter ` is present
        if (argIncludeRemark.find('`') != std::string::npos) 
        {
            // Split argIncludeRemark using ` as the delimiter
            string_array splitIncludeRemarks = split(argIncludeRemark, "`");
            // Filter out invalid regular expressions
            string_array tempValidRemarks;
            for (const auto& remark : splitIncludeRemarks)
             {
                if (!remark.empty() && regValid(remark)) // Validate each split element using regValid
                { 
                    tempValidRemarks.push_back(remark);
                }
            }
            if (!tempValidRemarks.empty())
                lIncludeRemarks = tempValidRemarks;
        }
        else
        {
            // If no delimiter is found, follow the original logic
            if (regValid(argIncludeRemark))
                lIncludeRemarks = string_array{argIncludeRemark};
        }
    }
    if (!argExcludeRemark.empty())
    {
        // Check if the delimiter ` is present
        if (argExcludeRemark.find('`') != std::string::npos)
        {
            // Split argExcludeRemark using ` as the delimiter
            string_array splitExcludeRemarks = split(argExcludeRemark, "`");
            // Filter out invalid regular expressions
            string_array tempValidRemarks;
            for (const auto& remark : splitExcludeRemarks)
            {
                if (!remark.empty() && regValid(remark)) // Validate each split element using regValid
                { 
                    tempValidRemarks.push_back(remark);
                }
            }
            if (!tempValidRemarks.empty())
                lExcludeRemarks = tempValidRemarks;
        }
        else
        {
            // If no delimiter is found, follow the original logic
            if (regValid(argExcludeRemark))
                lExcludeRemarks = string_array{argExcludeRemark};
        }
    }

    /// initialize script runtime
    if(authorized && !global.scriptCleanContext)
    {
        ext.js_runtime = new qjs::Runtime();
        script_runtime_init(*ext.js_runtime);
        ext.js_context = new qjs::Context(*ext.js_runtime);
        script_context_init(*ext.js_context);
    }

    //start parsing urls
    RegexMatchConfigs stream_temp = safe_get_streams(), time_temp = safe_get_times();

    //loading urls
    string_array urls;
    std::vector<Proxy> nodes, insert_nodes;
    int groupID = 0;

    parse_settings parse_set;
    parse_set.proxy = &proxy;
    parse_set.exclude_remarks = &lExcludeRemarks;
    parse_set.include_remarks = &lIncludeRemarks;
    parse_set.stream_rules = &stream_temp;
    parse_set.time_rules = &time_temp;
    parse_set.sub_info = &subInfo;
    parse_set.authorized = authorized;
    // 允许传递用户头部给机场
    parse_set.request_header = &request.headers;
    parse_set.custom_user_agent = &argUserAgent;
    parse_set.js_runtime = ext.js_runtime;
    parse_set.js_context = ext.js_context;

    if(!global.insertUrls.empty() && argEnableInsert)
    {
        groupID = -1;
        urls = split(global.insertUrls, "|");
        // Remove empty urls
        urls.erase(std::remove_if(urls.begin(), urls.end(), [](const std::string& str) { return str.empty(); }), urls.end());
        importItems(urls, true);
        for(std::string &x : urls)
        {
            x = regTrim(x);
            writeLog(0, "Fetching node data from url '" + x + "'.", LOG_LEVEL_INFO);
            if(addNodes(x, insert_nodes, groupID, parse_set) == -1)
            {
                if(global.skipFailedLinks)
                    writeLog(0, "The following link doesn't contain any valid node info: " + x, LOG_LEVEL_WARNING);
                else
                {
                    *status_code = 400;
                    return "The following link doesn't contain any valid node info: " + x;
                }
            }
            groupID--;
        }
    }
    urls = split(argUrl, "|");
    // Remove empty urls
    urls.erase(std::remove_if(urls.begin(), urls.end(), [](const std::string& str) { return str.empty(); }), urls.end());
    importItems(urls, true);
    groupID = 0;
    for(std::string &x : urls)
    {
        x = regTrim(x);
        //std::cerr<<"Fetching node data from url '"<<x<<"'."<<std::endl;
        writeLog(0, "Fetching node data from url '" + x + "'.", LOG_LEVEL_INFO);
        if(addNodes(x, nodes, groupID, parse_set) == -1)
        {
            if(global.skipFailedLinks)
                writeLog(0, "The following link doesn't contain any valid node info: " + x, LOG_LEVEL_WARNING);
            else
            {
                *status_code = 400;
                return "The following link doesn't contain any valid node info: " + x;
            }
        }
        groupID++;
    }
    //exit if found nothing
    if(nodes.empty() && insert_nodes.empty())
    {
        *status_code = 400;
        return "No nodes were found!";
    }
    if(!subInfo.empty() && argAppendUserinfo.get(global.appendUserinfo))
        response.headers.emplace("Subscription-UserInfo", subInfo);

    if(request.method == "HEAD")
        return "";

    argPrependInsert.define(global.prependInsert);
    if(argPrependInsert)
    {
        std::move(nodes.begin(), nodes.end(), std::back_inserter(insert_nodes));
        nodes.swap(insert_nodes);
    }
    else
    {
        std::move(insert_nodes.begin(), insert_nodes.end(), std::back_inserter(nodes));
    }
    //run filter script
    std::string filterScript = global.filterScript;
    if(authorized && !argFilterScript.empty())
        filterScript = argFilterScript;
    if(!filterScript.empty())
    {
        if(startsWith(filterScript, "path:"))
            filterScript = fileGet(filterScript.substr(5), false);
        /*
        duk_context *ctx = duktape_init();
        if(ctx)
        {
            defer(duk_destroy_heap(ctx);)
            if(duktape_peval(ctx, filterScript) == 0)
            {
                auto filter = [&](const Proxy &x)
                {
                    duk_get_global_string(ctx, "filter");
                    duktape_push_Proxy(ctx, x);
                    duk_pcall(ctx, 1);
                    return !duktape_get_res_bool(ctx);
                };
                nodes.erase(std::remove_if(nodes.begin(), nodes.end(), filter), nodes.end());
            }
            else
            {
                writeLog(0, "Error when trying to parse script:\n" + duktape_get_err_stack(ctx), LOG_LEVEL_ERROR);
                duk_pop(ctx); /// pop err
            }
        }
        */
        script_safe_runner(ext.js_runtime, ext.js_context, [&](qjs::Context &ctx)
        {
            try
            {
                ctx.eval(filterScript);
                auto filter = (std::function<bool(const Proxy&)>) ctx.eval("filter");
                nodes.erase(std::remove_if(nodes.begin(), nodes.end(), filter), nodes.end());
            }
            catch(qjs::exception)
            {
                script_print_stack(ctx);
            }
        }, global.scriptCleanContext);
    }

    //check custom group name
    if(!argGroupName.empty())
        for(Proxy &x : nodes)
            x.Group = argGroupName;

    //do pre-process now
    preprocessNodes(nodes, ext);

    /*
    //insert node info to template
    int index = 0;
    std::string template_node_prefix;
    for(Proxy &x : nodes)
    {
        template_node_prefix = std::to_string(index) + ".";
        tpl_args.node_list[template_node_prefix + "remarks"] = x.remarks;
        tpl_args.node_list[template_node_prefix + "group"] = x.Group;
        tpl_args.node_list[template_node_prefix + "groupid"] = std::to_string(x.GroupId);
        index++;
    }
    */

    ProxyGroupConfigs dummy_group;
    std::vector<RulesetContent> dummy_ruleset;
    std::string managed_url = base64Decode(getUrlArg(argument, "profile_data"));
    if(managed_url.empty())
        managed_url = global.managedConfigPrefix + "/sub?" + joinArguments(argument);

    //std::cerr<<"Generate target: ";
    proxy = parseProxy(global.proxyConfig);
    switch(hash_(argTarget))
    {
    case "clash"_hash: case "clashr"_hash:
        writeLog(0, argTarget == "clashr" ? "Generate target: ClashR" : "Generate target: Clash", LOG_LEVEL_INFO);
        tpl_args.local_vars["clash.new_field_name"] = ext.clash_new_field_name ? "true" : "false";
        response.headers["profile-update-interval"] = std::to_string(interval / 3600);
        if(ext.nodelist)
        {
            YAML::Node yamlnode;
            proxyToClash(nodes, yamlnode, dummy_group, argTarget == "clashr", ext);
            output_content = YAML::Dump(yamlnode);
        }
        else
        {
            if(render_template(fetchFile(lClashBase, proxy, global.cacheConfig), tpl_args, base_content, global.templatePath) != 0)
            {
                *status_code = 400;
                return base_content;
            }
            output_content = proxyToClash(nodes, base_content, lRulesetContent, lCustomProxyGroups, argTarget == "clashr", ext);
        }

        if(argUpload)
            uploadGist(argTarget, argUploadPath, output_content, false);
        break;
    case "surge"_hash:

        writeLog(0, "Generate target: Surge " + std::to_string(intSurgeVer), LOG_LEVEL_INFO);

        if(ext.nodelist)
        {
            output_content = proxyToSurge(nodes, base_content, dummy_ruleset, dummy_group, intSurgeVer, ext);

            if(argUpload)
                uploadGist("surge" + argSurgeVer + "list", argUploadPath, output_content, true);
        }
        else
        {
            if(render_template(fetchFile(lSurgeBase, proxy, global.cacheConfig), tpl_args, base_content, global.templatePath) != 0)
            {
                *status_code = 400;
                return base_content;
            }
            output_content = proxyToSurge(nodes, base_content, lRulesetContent, lCustomProxyGroups, intSurgeVer, ext);

            if(argUpload)
                uploadGist("surge" + argSurgeVer, argUploadPath, output_content, true);

            if(global.writeManagedConfig && !global.managedConfigPrefix.empty())
                output_content = "#!MANAGED-CONFIG " + managed_url + (interval ? " interval=" + std::to_string(interval) : "") \
                 + " strict=" + std::string(strict ? "true" : "false") + "\n\n" + output_content;
        }
        break;
    case "surfboard"_hash:
        writeLog(0, "Generate target: Surfboard", LOG_LEVEL_INFO);

        if(render_template(fetchFile(lSurfboardBase, proxy, global.cacheConfig), tpl_args, base_content, global.templatePath) != 0)
        {
            *status_code = 400;
            return base_content;
        }
        output_content = proxyToSurge(nodes, base_content, lRulesetContent, lCustomProxyGroups, -3, ext);
        if(argUpload)
            uploadGist("surfboard", argUploadPath, output_content, true);

        if(global.writeManagedConfig && !global.managedConfigPrefix.empty())
            output_content = "#!MANAGED-CONFIG " + managed_url + (interval ? " interval=" + std::to_string(interval) : "") \
                 + " strict=" + std::string(strict ? "true" : "false") + "\n\n" + output_content;
        break;
    case "mellow"_hash:
        writeLog(0, "Generate target: Mellow", LOG_LEVEL_INFO);

        if(render_template(fetchFile(lMellowBase, proxy, global.cacheConfig), tpl_args, base_content, global.templatePath) != 0)
        {
            *status_code = 400;
            return base_content;
        }
        output_content = proxyToMellow(nodes, base_content, lRulesetContent, lCustomProxyGroups, ext);

        if(argUpload)
            uploadGist("mellow", argUploadPath, output_content, true);
        break;
    case "sssub"_hash:
        writeLog(0, "Generate target: SS Subscription", LOG_LEVEL_INFO);

        if(render_template(fetchFile(lSSSubBase, proxy, global.cacheConfig), tpl_args, base_content, global.templatePath) != 0)
        {
            *status_code = 400;
            return base_content;
        }
        output_content = proxyToSSSub(base_content, nodes, ext);
        if(argUpload)
            uploadGist("sssub", argUploadPath, output_content, false);
        break;
    case "ss"_hash:
        writeLog(0, "Generate target: SS", LOG_LEVEL_INFO);
        output_content = proxyToSingle(nodes, 1, ext);
        if(argUpload)
            uploadGist("ss", argUploadPath, output_content, false);
        break;
    case "ssr"_hash:
        writeLog(0, "Generate target: SSR", LOG_LEVEL_INFO);
        output_content = proxyToSingle(nodes, 2, ext);
        if(argUpload)
            uploadGist("ssr", argUploadPath, output_content, false);
        break;
    case "v2ray"_hash:
        writeLog(0, "Generate target: v2rayN", LOG_LEVEL_INFO);
        output_content = proxyToSingle(nodes, 4, ext);
        if(argUpload)
            uploadGist("v2ray", argUploadPath, output_content, false);
        break;
    case "trojan"_hash:
        writeLog(0, "Generate target: Trojan", LOG_LEVEL_INFO);
        output_content = proxyToSingle(nodes, 8, ext);
        if(argUpload)
            uploadGist("trojan", argUploadPath, output_content, false);
        break;
    case "mixed"_hash:
        writeLog(0, "Generate target: Standard Subscription", LOG_LEVEL_INFO);
        output_content = proxyToSingle(nodes, 15, ext);
        if(argUpload)
            uploadGist("sub", argUploadPath, output_content, false);
        break;
    case "quan"_hash:
        writeLog(0, "Generate target: Quantumult", LOG_LEVEL_INFO);
        if(!ext.nodelist)
        {
            if(render_template(fetchFile(lQuanBase, proxy, global.cacheConfig), tpl_args, base_content, global.templatePath) != 0)
            {
                *status_code = 400;
                return base_content;
            }
        }

        output_content = proxyToQuan(nodes, base_content, lRulesetContent, lCustomProxyGroups, ext);

        if(argUpload)
            uploadGist("quan", argUploadPath, output_content, false);
        break;
    case "quanx"_hash:
        writeLog(0, "Generate target: Quantumult X", LOG_LEVEL_INFO);
        if(!ext.nodelist)
        {
            if(render_template(fetchFile(lQuanXBase, proxy, global.cacheConfig), tpl_args, base_content, global.templatePath) != 0)
            {
                *status_code = 400;
                return base_content;
            }
        }

        output_content = proxyToQuanX(nodes, base_content, lRulesetContent, lCustomProxyGroups, ext);

        if(argUpload)
            uploadGist("quanx", argUploadPath, output_content, false);
        break;
    case "loon"_hash:
        writeLog(0, "Generate target: Loon", LOG_LEVEL_INFO);
        if(!ext.nodelist)
        {
            if(render_template(fetchFile(lLoonBase, proxy, global.cacheConfig), tpl_args, base_content, global.templatePath) != 0)
            {
                *status_code = 400;
                return base_content;
            }
        }

        output_content = proxyToLoon(nodes, base_content, lRulesetContent, lCustomProxyGroups, ext);

        if(argUpload)
            uploadGist("loon", argUploadPath, output_content, false);
        break;
    case "ssd"_hash:
        writeLog(0, "Generate target: SSD", LOG_LEVEL_INFO);
        output_content = proxyToSSD(nodes, argGroupName, subInfo, ext);
        if(argUpload)
            uploadGist("ssd", argUploadPath, output_content, false);
        break;
    case "singbox"_hash:
        writeLog(0, "Generate target: sing-box", LOG_LEVEL_INFO);
        if(!ext.nodelist)
        {
            if(render_template(fetchFile(lSingBoxBase, proxy, global.cacheConfig), tpl_args, base_content, global.templatePath) != 0)
            {
                *status_code = 400;
                return base_content;
            }
        }

        output_content = proxyToSingBox(nodes, base_content, lRulesetContent, lCustomProxyGroups, ext);

        if(argUpload)
            uploadGist("singbox", argUploadPath, output_content, false);
        break;
    default:
        writeLog(0, "Generate target: Unspecified", LOG_LEVEL_INFO);
        *status_code = 500;
        return "Unrecognized target";
    }
    writeLog(0, "Generate completed.", LOG_LEVEL_INFO);
    if(!argFilename.empty())
        response.headers.emplace("Content-Disposition", "attachment; filename=\"" + argFilename + "\"; filename*=utf-8''" + urlEncode(argFilename));
    return output_content;
}

std::string simpleToClashR(RESPONSE_CALLBACK_ARGS)
{
    auto argument = joinArguments(request.argument);
    int *status_code = &response.status_code;

    std::string url = argument.size() <= 8 ? "" : argument.substr(8);
    if(url.empty() || argument.substr(0, 8) != "sublink=")
    {
        *status_code = 400;
        return "Invalid request!";
    }
    if(url == "sublink")
    {
        *status_code = 400;
        return "Please insert your subscription link instead of clicking the default link.";
    }
    request.argument.emplace("target", "clashr");
    request.argument.emplace("url", url);
    return subconverter(request, response);
}

std::string surgeConfToClash(RESPONSE_CALLBACK_ARGS)
{
    auto argument = joinArguments(request.argument);
    int *status_code = &response.status_code;

    INIReader ini;
    string_array dummy_str_array;
    std::vector<Proxy> nodes;
    std::string base_content, url = argument.size() <= 5 ? "" : argument.substr(5);
    const std::string proxygroup_name = global.clashUseNewField ? "proxy-groups" : "Proxy Group", rule_name = global.clashUseNewField ? "rules" : "Rule";

    ini.store_any_line = true;

    if(url.empty())
        url = global.defaultUrls;
    if(url.empty() || argument.substr(0, 5) != "link=")
    {
        *status_code = 400;
        return "Invalid request!";
    }
    if(url == "link")
    {
        *status_code = 400;
        return "Please insert your subscription link instead of clicking the default link.";
    }
    writeLog(0, "SurgeConfToClash called with url '" + url + "'.", LOG_LEVEL_INFO);

    std::string proxy = parseProxy(global.proxyConfig);
    YAML::Node clash;
    template_args tpl_args;
    tpl_args.global_vars = global.templateVars;
    tpl_args.local_vars["clash.new_field_name"] = global.clashUseNewField ? "true" : "false";
    tpl_args.request_params["target"] = "clash";
    tpl_args.request_params["url"] = url;

    if(render_template(fetchFile(global.clashBase, proxy, global.cacheConfig), tpl_args, base_content, global.templatePath) != 0)
    {
        *status_code = 400;
        return base_content;
    }
    clash = YAML::Load(base_content);

    base_content = fetchFile(url, proxy, global.cacheConfig);

    if(ini.parse(base_content) != INIREADER_EXCEPTION_NONE)
    {
        std::string errmsg = "Parsing Surge config failed! Reason: " + ini.get_last_error();
        //std::cerr<<errmsg<<"\n";
        writeLog(0, errmsg, LOG_LEVEL_ERROR);
        *status_code = 400;
        return errmsg;
    }
    if(!ini.section_exist("Proxy") || !ini.section_exist("Proxy Group") || !ini.section_exist("Rule"))
    {
        std::string errmsg = "Incomplete surge config! Missing critical sections!";
        //std::cerr<<errmsg<<"\n";
        writeLog(0, errmsg, LOG_LEVEL_ERROR);
        *status_code = 400;
        return errmsg;
    }

    //scan groups first, get potential policy-path
    string_multimap section;
    ini.get_items("Proxy Group", section);
    std::string name, type, content;
    string_array links;
    links.emplace_back(url);
    YAML::Node singlegroup;
    for(auto &x : section)
    {
        singlegroup.reset();
        name = x.first;
        content = x.second;
        dummy_str_array = split(content, ",");
        if(dummy_str_array.empty())
            continue;
        type = dummy_str_array[0];
        if(!(type == "select" || type == "url-test" || type == "fallback" || type == "load-balance")) //remove unsupported types
            continue;
        singlegroup["name"] = name;
        singlegroup["type"] = type;
        for(unsigned int i = 1; i < dummy_str_array.size(); i++)
        {
            if(startsWith(dummy_str_array[i], "url"))
                singlegroup["url"] = trim(dummy_str_array[i].substr(dummy_str_array[i].find('=') + 1));
            else if(startsWith(dummy_str_array[i], "interval"))
                singlegroup["interval"] = trim(dummy_str_array[i].substr(dummy_str_array[i].find('=') + 1));
            else if(startsWith(dummy_str_array[i], "policy-path"))
                links.emplace_back(trim(dummy_str_array[i].substr(dummy_str_array[i].find('=') + 1)));
            else
                singlegroup["proxies"].push_back(trim(dummy_str_array[i]));
        }
        clash[proxygroup_name].push_back(singlegroup);
    }

    proxy = parseProxy(global.proxySubscription);
    eraseElements(dummy_str_array);

    RegexMatchConfigs dummy_regex_array;
    std::string subInfo;
    parse_settings parse_set;
    parse_set.proxy = &proxy;
    parse_set.exclude_remarks = parse_set.include_remarks = &dummy_str_array;
    parse_set.stream_rules = parse_set.time_rules = &dummy_regex_array;
    parse_set.request_header = nullptr;
    parse_set.sub_info = &subInfo;
    parse_set.authorized = !global.APIMode;
    for(std::string &x : links)
    {
        //std::cerr<<"Fetching node data from url '"<<x<<"'."<<std::endl;
        writeLog(0, "Fetching node data from url '" + x + "'.", LOG_LEVEL_INFO);
        if(addNodes(x, nodes, 0, parse_set) == -1)
        {
            if(global.skipFailedLinks)
                writeLog(0, "The following link doesn't contain any valid node info: " + x, LOG_LEVEL_WARNING);
            else
            {
                *status_code = 400;
                return "The following link doesn't contain any valid node info: " + x;
            }
        }
    }

    //exit if found nothing
    if(nodes.empty())
    {
        *status_code = 400;
        return "No nodes were found!";
    }

    extra_settings ext;
    ext.sort_flag = global.enableSort;
    ext.filter_deprecated = global.filterDeprecated;
    ext.clash_new_field_name = global.clashUseNewField;
    ext.udp = global.UDPFlag;
    ext.tfo = global.TFOFlag;
    ext.skip_cert_verify = global.skipCertVerify;
    ext.tls13 = global.TLS13Flag;
    ext.clash_proxies_style = global.clashProxiesStyle;
    ext.clash_proxy_groups_style = global.clashProxyGroupsStyle;

    ProxyGroupConfigs dummy_groups;
    proxyToClash(nodes, clash, dummy_groups, false, ext);

    section.clear();
    ini.get_items("Proxy", section);
    for(auto &x : section)
    {
        singlegroup.reset();
        name = x.first;
        content = x.second;
        dummy_str_array = split(content, ",");
        if(dummy_str_array.empty())
            continue;
        content = trim(dummy_str_array[0]);
        switch(hash_(content))
        {
        case "direct"_hash:
            singlegroup["name"] = name;
            singlegroup["type"] = "select";
            singlegroup["proxies"].push_back("DIRECT");
            break;
        case "reject"_hash:
        case "reject-tinygif"_hash:
            singlegroup["name"] = name;
            singlegroup["type"] = "select";
            singlegroup["proxies"].push_back("REJECT");
            break;
        default:
            continue;
        }
        clash[proxygroup_name].push_back(singlegroup);
    }

    eraseElements(dummy_str_array);
    ini.get_all("Rule", "{NONAME}", dummy_str_array);
    YAML::Node rule;
    string_array strArray;
    std::string strLine;
    std::stringstream ss;
    std::string::size_type lineSize;
    for(std::string &x : dummy_str_array)
    {
        if(startsWith(x, "RULE-SET"))
        {
            strArray = split(x, ",");
            if(strArray.size() != 3)
                continue;
            content = webGet(strArray[1], proxy, global.cacheRuleset);
            if(content.empty())
                continue;

            ss << content;
            char delimiter = getLineBreak(content);

            while(getline(ss, strLine, delimiter))
            {
                lineSize = strLine.size();
                if(lineSize && strLine[lineSize - 1] == '\r') //remove line break
                    strLine.erase(--lineSize);
                if(!lineSize || strLine[0] == ';' || strLine[0] == '#' || (lineSize >= 2 && strLine[0] == '/' && strLine[1] == '/')) //empty lines and comments are ignored
                    continue;
                else if(!std::any_of(ClashRuleTypes.begin(), ClashRuleTypes.end(), [&strLine](const std::string& type){return startsWith(strLine, type);})) //remove unsupported types
                    continue;
                strLine += strArray[2];
                if(count_least(strLine, ',', 3))
                    strLine = regReplace(strLine, "^(.*?,.*?)(,.*)(,.*)$", "$1$3$2");
                rule.push_back(strLine);
            }
            ss.clear();
            continue;
        }
        else if(!std::any_of(ClashRuleTypes.begin(), ClashRuleTypes.end(), [&strLine](const std::string& type){return startsWith(strLine, type);}))
            continue;
        rule.push_back(x);
    }
    clash[rule_name] = rule;

    response.headers["profile-update-interval"] = std::to_string(global.updateInterval / 3600);
    writeLog(0, "Conversion completed.", LOG_LEVEL_INFO);
    return YAML::Dump(clash);
}

// Merge multiple key-values based on delimiters
static void merge_values(const string_multimap& source, 
                 const std::string& key,
                 std::string& merged,
                 char delimiter)
{
    auto range = source.equal_range(key);
    for (auto iter = range.first; iter != range.second; ++iter)
    {
        if (!iter->second.empty())
        {
            if (!merged.empty()) merged += delimiter;
            merged += iter->second;
        }
    }
}

// Update container
static void update_container(string_multimap& container, 
                     const std::string& key,
                     const std::string& merged)
{
    if (!merged.empty()) {
        container.erase(key);
        container.emplace(key, merged);
    }
}

std::string getProfile(RESPONSE_CALLBACK_ARGS)
{
    auto &argument = request.argument;
    int *status_code = &response.status_code;

    std::string name = getUrlArg(argument, "name"), token = getUrlArg(argument, "token");
    string_array profiles = split(name, "|");
    if(token.empty() || profiles.empty())
    {
        *status_code = 403;
        return "Forbidden";
    }
    std::string profile_content;
    name = profiles[0];
    /*if(vfs::vfs_exist(name))
    {
        profile_content = vfs::vfs_get(name);
    }
    else */
    if(fileExist(name))
    {
        profile_content = fileGet(name, true);
    }
    else
    {
        *status_code = 404;
        return "Profile not found";
    }
    //std::cerr<<"Trying to load profile '" + name + "'.\n";
    writeLog(0, "Trying to load profile '" + name + "'.", LOG_LEVEL_INFO);
    INIReader ini;
    if(ini.parse(profile_content) != INIREADER_EXCEPTION_NONE && !ini.section_exist("Profile"))
    {
        //std::cerr<<"Load profile failed! Reason: "<<ini.get_last_error()<<"\n";
        writeLog(0, "Load profile failed! Reason: " + ini.get_last_error(), LOG_LEVEL_ERROR);
        *status_code = 500;
        return "Broken profile!";
    }
    //std::cerr<<"Trying to parse profile '" + name + "'.\n";
    writeLog(0, "Trying to parse profile '" + name + "'.", LOG_LEVEL_INFO);
    string_multimap contents;
    ini.get_items("Profile", contents);
    if(contents.empty())
    {
        //std::cerr<<"Load profile failed! Reason: Empty Profile section\n";
        writeLog(0, "Load profile failed! Reason: Empty Profile section", LOG_LEVEL_ERROR);
        *status_code = 500;
        return "Broken profile!";
    }
    auto profile_token = contents.find("profile_token");
    if(profiles.size() == 1 && profile_token != contents.end())
    {
        if(token != profile_token->second)
        {
            *status_code = 403;
            return "Forbidden";
        }
        token = global.accessToken;
    }
    else
    {
        if(token != global.accessToken)
        {
            *status_code = 403;
            return "Forbidden";
        }
    }
    
    // Handle url
    std::string all_urls;
    merge_values(contents, "url", all_urls, '|');
    if(profiles.size() > 1)
    {
        writeLog(0, "Multiple profiles are provided. Trying to combine profiles...", LOG_TYPE_INFO);
        for(size_t i = 1; i < profiles.size(); i++)
        {
            name = profiles[i];
            if(!fileExist(name))
            {
                writeLog(0, "Ignoring non-exist profile '" + name + "'...", LOG_LEVEL_WARNING);
                continue;
            }
            if(ini.parse_file(name) != INIREADER_EXCEPTION_NONE && !ini.section_exist("Profile"))
            {
                writeLog(0, "Ignoring broken profile '" + name + "'...", LOG_LEVEL_WARNING);
                continue;
            }
            string_multimap profile_items;
            ini.get_items("Profile", profile_items); // Get all key-value pairs in the [Profile] section
            size_t before_length = all_urls.length();
            merge_values(profile_items, "url", all_urls, '|');
            if (all_urls.length() > before_length) 
            {
                writeLog(0, "Profile url from '" + name + "' added.", LOG_LEVEL_INFO);
            }
            else
            {
                writeLog(0, "Profile '" + name + "' does not have url key. Skipping...", LOG_LEVEL_INFO);
            }
        }
    } 
    // Update the url key-value pairs in contents uniformly
    update_container(contents, "url", all_urls);

    // Handle rename
    std::string all_renames;
    merge_values(contents, "rename", all_renames, '`');
    update_container(contents, "rename", all_renames);

    // Handle exclude
    std::string all_excludes;
    merge_values(contents, "exclude", all_excludes, '`');
    update_container(contents, "exclude", all_excludes);

    // Handle include
    std::string all_includes;
    merge_values(contents, "include", all_includes, '`');
    update_container(contents, "include", all_includes);

    contents.emplace("token", token);
    contents.emplace("profile_data", base64Encode(global.managedConfigPrefix + "/getprofile?" + joinArguments(argument)));
    std::copy(argument.cbegin(), argument.cend(), std::inserter(contents, contents.end()));
    request.argument = contents;
    return subconverter(request, response);
}

/*
std::string jinja2_webGet(const std::string &url)
{
    std::string proxy = parseProxy(global.proxyConfig);
    writeLog(0, "Template called fetch with url '" + url + "'.", LOG_LEVEL_INFO);
    return webGet(url, proxy, global.cacheConfig);
}*/

inline std::string intToStream(unsigned long long stream)
{
    char chrs[16] = {}, units[6] = {' ', 'K', 'M', 'G', 'T', 'P'};
    double streamval = stream;
    unsigned int level = 0;
    while(streamval > 1024.0)
    {
        if(level >= 5)
            break;
        level++;
        streamval /= 1024.0;
    }
    snprintf(chrs, 15, "%.2f %cB", streamval, units[level]);
    return {chrs};
}

std::string subInfoToMessage(std::string subinfo)
{
    using ull = unsigned long long;
    subinfo = replaceAllDistinct(subinfo, "; ", "&");
    std::string retdata, useddata = "N/A", totaldata = "N/A", expirydata = "N/A";
    std::string upload = getUrlArg(subinfo, "upload"), download = getUrlArg(subinfo, "download"), total = getUrlArg(subinfo, "total"), expire = getUrlArg(subinfo, "expire");
    ull used = to_number<ull>(upload, 0) + to_number<ull>(download, 0), tot = to_number<ull>(total, 0);
    auto expiry = to_number<time_t>(expire, 0);
    if(used != 0)
        useddata = intToStream(used);
    if(tot != 0)
        totaldata = intToStream(tot);
    if(expiry != 0)
    {
        char buffer[30];
        struct tm *dt = localtime(&expiry);
        strftime(buffer, sizeof(buffer), "%Y-%m-%d %H:%M", dt);
        expirydata.assign(buffer);
    }
    if(useddata == "N/A" && totaldata == "N/A" && expirydata == "N/A")
        retdata = "Not Available";
    else
        retdata += "Stream Used: " + useddata + " Stream Total: " + totaldata + " Expiry Time: " + expirydata;
    return retdata;
}

int simpleGenerator()
{
    //std::cerr<<"\nReading generator configuration...\n";
    writeLog(0, "Reading generator configuration...", LOG_LEVEL_INFO);
    std::string config = fileGet("generate.ini"), path, profile, content;
    if(config.empty())
    {
        //std::cerr<<"Generator configuration not found or empty!\n";
        writeLog(0, "Generator configuration not found or empty!", LOG_LEVEL_ERROR);
        return -1;
    }

    INIReader ini;
    if(ini.parse(config) != INIREADER_EXCEPTION_NONE)
    {
        //std::cerr<<"Generator configuration broken! Reason:"<<ini.get_last_error()<<"\n";
        writeLog(0, "Generator configuration broken! Reason:" + ini.get_last_error(), LOG_LEVEL_ERROR);
        return -2;
    }
    //std::cerr<<"Read generator configuration completed.\n\n";
    writeLog(0, "Read generator configuration completed.\n", LOG_LEVEL_INFO);

    string_array sections = ini.get_section_names();
    if(!global.generateProfiles.empty())
    {
        //std::cerr<<"Generating with specific artifacts: \""<<gen_profile<<"\"...\n";
        writeLog(0, "Generating with specific artifacts: \"" + global.generateProfiles + "\"...", LOG_LEVEL_INFO);
        string_array targets = split(global.generateProfiles, ","), new_targets;
        for(std::string &x : targets)
        {
            x = trim(x);
            if(std::find(sections.cbegin(), sections.cend(), x) != sections.cend())
                new_targets.emplace_back(std::move(x));
            else
            {
                //std::cerr<<"Artifact \""<<x<<"\" not found in generator settings!\n";
                writeLog(0, "Artifact \"" + x + "\" not found in generator settings!", LOG_LEVEL_ERROR);
                return -3;
            }
        }
        sections = new_targets;
        sections.shrink_to_fit();
    }
    else
        //std::cerr<<"Generating all artifacts...\n";
        writeLog(0, "Generating all artifacts...", LOG_LEVEL_INFO);

    string_multimap allItems;
    std::string proxy = parseProxy(global.proxySubscription);
    for(std::string &x : sections)
    {
        Request request;
        Response response;
        response.status_code = 200;
        //std::cerr<<"Generating artifact '"<<x<<"'...\n";
        writeLog(0, "Generating artifact '" + x + "'...", LOG_LEVEL_INFO);
        ini.enter_section(x);
        if(ini.item_exist("path"))
            path = ini.get("path");
        else
        {
            //std::cerr<<"Artifact '"<<x<<"' output path missing! Skipping...\n\n";
            writeLog(0, "Artifact '" + x + "' output path missing! Skipping...\n", LOG_LEVEL_ERROR);
            continue;
        }
        if(ini.item_exist("profile"))
        {
            profile = ini.get("profile");
            request.argument.emplace("name", profile);
            request.argument.emplace("token", global.accessToken);
            request.argument.emplace("expand", "true");
            content = getProfile(request, response);
        }
        else
        {
            if(ini.get_bool("direct"))
            {
                std::string url = ini.get("url");
                content = fetchFile(url, proxy, global.cacheSubscription);
                if(content.empty())
                {
                    //std::cerr<<"Artifact '"<<x<<"' generate ERROR! Please check your link.\n\n";
                    writeLog(0, "Artifact '" + x + "' generate ERROR! Please check your link.\n", LOG_LEVEL_ERROR);
                    if(sections.size() == 1)
                        return -1;
                }
                // add UTF-8 BOM
                fileWrite(path, "\xEF\xBB\xBF" + content, true);
                continue;
            }
            ini.get_items(allItems);
            allItems.emplace("expand", "true");
            for(auto &y : allItems)
            {
                if(y.first == "path")
                    continue;
                request.argument.emplace(y.first, y.second);
            }
            content = subconverter(request, response);
        }
        if(response.status_code != 200)
        {
            //std::cerr<<"Artifact '"<<x<<"' generate ERROR! Reason: "<<content<<"\n\n";
            writeLog(0, "Artifact '" + x + "' generate ERROR! Reason: " + content + "\n", LOG_LEVEL_ERROR);
            if(sections.size() == 1)
                return -1;
            continue;
        }
        fileWrite(path, content, true);
        auto iter = std::find_if(response.headers.begin(), response.headers.end(), [](auto y){ return y.first == "Subscription-UserInfo"; });
        if(iter != response.headers.end())
            writeLog(0, "User Info for artifact '" + x + "': " + subInfoToMessage(iter->second), LOG_LEVEL_INFO);
        //std::cerr<<"Artifact '"<<x<<"' generate SUCCESS!\n\n";
        writeLog(0, "Artifact '" + x + "' generate SUCCESS!\n", LOG_LEVEL_INFO);
        eraseElements(response.headers);
    }
    //std::cerr<<"All artifact generated. Exiting...\n";
    writeLog(0, "All artifact generated. Exiting...", LOG_LEVEL_INFO);
    return 0;
}

std::string renderTemplate(RESPONSE_CALLBACK_ARGS)
{
    auto &argument = request.argument;
    int *status_code = &response.status_code;

    std::string path = getUrlArg(argument, "path");
    writeLog(0, "Trying to render template '" + path + "'...", LOG_LEVEL_INFO);

    if(!startsWith(path, global.templatePath) || !fileExist(path))
    {
        *status_code = 404;
        return "Not found";
    }
    std::string template_content = fetchFile(path, parseProxy(global.proxyConfig), global.cacheConfig);
    if(template_content.empty())
    {
        *status_code = 400;
        return "File empty or out of scope";
    }
    template_args tpl_args;
    tpl_args.global_vars = global.templateVars;

    //load request arguments as template variables
    string_map req_arg_map;
    for (auto &x : argument)
    {
        req_arg_map[x.first] = x.second;
    }
    tpl_args.request_params = req_arg_map;

    std::string output_content;
    if(render_template(template_content, tpl_args, output_content, global.templatePath) != 0)
    {
        *status_code = 400;
        writeLog(0, "Render failed with error.", LOG_LEVEL_WARNING);
    }
    else
        writeLog(0, "Render completed.", LOG_LEVEL_INFO);

    return output_content;
}
