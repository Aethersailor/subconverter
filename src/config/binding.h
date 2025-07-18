#ifndef BINDING_H_INCLUDED
#define BINDING_H_INCLUDED

#include <toml.hpp>

#include "handler/settings.h"
#include "crontask.h"
#include "proxygroup.h"
#include "regmatch.h"
#include "ruleset.h"

namespace toml
{
    template<>
    struct from<ProxyGroupConfig>
    {
        static ProxyGroupConfig from_toml(const value& v)
        {
            ProxyGroupConfig conf;
            conf.Name = find<String>(v, "name");
            String type = find<String>(v, "type");
            String strategy = find_or<String>(v, "strategy", "");
            switch(hash_(type))
            {
            case "select"_hash:
                conf.Type = ProxyGroupType::Select;
                break;
            case "url-test"_hash:
                conf.Type = ProxyGroupType::URLTest;
                conf.Url = find<String>(v, "url");
                conf.Interval = find<Integer>(v, "interval");
                conf.Tolerance = find_or<Integer>(v, "tolerance", 0);
                if(v.contains("lazy"))
                    conf.Lazy = find_or<bool>(v, "lazy", false);
                if(v.contains("evaluate-before-use"))
                    conf.EvaluateBeforeUse = find_or(v, "evaluate-before-use", conf.EvaluateBeforeUse.get());
                break;
            case "load-balance"_hash:
                conf.Type = ProxyGroupType::LoadBalance;
                conf.Url = find<String>(v, "url");
                conf.Interval = find<Integer>(v, "interval");
                switch(hash_(strategy))
                {
                case "consistent-hashing"_hash:
                    conf.Strategy = BalanceStrategy::ConsistentHashing;
                    break;
                case "round-robin"_hash:
                    conf.Strategy = BalanceStrategy::RoundRobin;
                    break;
                }
                if(v.contains("persistent"))
                    conf.Persistent = find_or(v, "persistent", conf.Persistent.get());
                break;
            case "fallback"_hash:
                conf.Type = ProxyGroupType::Fallback;
                conf.Url = find<String>(v, "url");
                conf.Interval = find<Integer>(v, "interval");
                if(v.contains("evaluate-before-use"))
                    conf.EvaluateBeforeUse = find_or(v, "evaluate-before-use", conf.EvaluateBeforeUse.get());
                break;
            case "relay"_hash:
                conf.Type = ProxyGroupType::Relay;
                break;
            case "ssid"_hash:
                conf.Type = ProxyGroupType::SSID;
                break;
            case "smart"_hash:
                conf.Type = ProxyGroupType::Smart;
                conf.Url = find<String>(v, "url");
                conf.Interval = find<Integer>(v, "interval");
                if(v.contains("uselightgbm"))
                    conf.UseLightGBM = find_or<bool>(v, "uselightgbm", true);
                if(v.contains("collectdata"))
                    conf.CollectData = find_or<bool>(v, "collectdata", false);
                if(v.contains("strategy"))
                {
                    String strategy = find<String>(v, "strategy");
                    if(strategy == "round-robin")
                        conf.SmartStrategyType = SmartStrategy::RoundRobin;
                    else
                        conf.SmartStrategyType = SmartStrategy::StickySessions;
                }
                if(v.contains("lazy"))
                    conf.Lazy = find_or<bool>(v, "lazy", false);
                if(v.contains("evaluate-before-use"))
                    conf.EvaluateBeforeUse = find_or(v, "evaluate-before-use", conf.EvaluateBeforeUse.get());
                if(v.contains("policy-priority")) {
                    String value = find<String>(v, "policy-priority");
                    if (!value.empty()) {
                        conf.PolicyPriority = value;
                        conf.PolicyPrioritySet = true;
                    }
                }
                break;
            default:
                throw serialization_error(format_error("Proxy Group has unsupported type!", v.at("type").location(), "should be one of following: select, url-test, load-balance, fallback, relay, ssid, smart"), v.at("type").location());
            }
            conf.Timeout = find_or(v, "timeout", 5);
            conf.Proxies = find_or<StrArray>(v, "rule", {});
            conf.UsingProvider = find_or<StrArray>(v, "use", {});
            if(conf.Proxies.empty() && conf.UsingProvider.empty())
                throw serialization_error(format_error("Proxy Group must contains at least one of proxy match rule or provider!", v.location(), "here"), v.location());
            if(v.contains("disable-udp"))
                conf.DisableUdp = find_or(v, "disable-udp", conf.DisableUdp.get());
            return conf;
        }
    };

    template<>
    struct from<RulesetConfig>
    {
        static RulesetConfig from_toml(const value& v)
        {
            RulesetConfig conf;
            conf.Group = find<String>(v, "group");
            String type = find_or<String>(v, "type", "surge-ruleset");
            switch(hash_(type))
            {
            /*
            case "surge-ruleset"_hash:
                conf.Type = RulesetType::SurgeRuleset;
                conf.Url = "surge:";
                break;
            case "quantumultx"_hash:
                conf.Type = RulesetType::QuantumultX;
                conf.Url = "quanx:";
                break;
            case "clash-domain"_hash:
                conf.Type = RulesetType::ClashDomain;
                conf.Url = type;
                break;
            case "clash-ipcidr"_hash:
                conf.Type = RulesetType::ClashIpCidr;
                conf.Url = type;
                break;
            case "clash-classic"_hash:
                conf.Type = RulesetType::ClashClassic;
                conf.Url = type;
                break;
            */
            case "surge-ruleset"_hash:
                conf.Url = "surge:";
                break;
            case "quantumultx"_hash:
                conf.Url = "quanx:";
                break;
            case "clash-domain"_hash:
            case "clash-ipcidr"_hash:
            case "clash-classic"_hash:
                conf.Url = type + ":";
                break;
            default:
                throw serialization_error(format_error("Ruleset has unsupported type!", v.at("type").location(), "should be one of following: surge-ruleset, quantumultx, clash-domain, clash-ipcidr, clash-classic"), v.at("type").location());
            }
            conf.Url += find<String>(v, "ruleset");
            conf.Interval = find_or<Integer>(v, "interval", 86400);
            return conf;
        }
    };

    template<>
    struct from<RegexMatchConfig>
    {
        static RegexMatchConfig from_toml(const value& v)
        {
            RegexMatchConfig conf;
            if(v.contains("script"))
            {
                conf.Script = find<String>(v, "script");
                return conf;
            }
            conf.Match = find<String>(v, "match");
            if(v.contains("emoji"))
                conf.Replace = find<String>(v, "emoji");
            else
                conf.Replace = find<String>(v, "replace");
            return conf;
        }
    };

    template<>
    struct from<CronTaskConfig>
    {
        static CronTaskConfig from_toml(const value& v)
        {
            CronTaskConfig conf;
            conf.Name = find<String>(v, "name");
            conf.CronExp = find<String>(v, "cronexp");
            conf.Path = find<String>(v, "path");
            conf.Timeout = find_or<Integer>(v, "timeout", 0);
            return conf;
        }
    };

    template<>
    struct from<tribool>
    {
        static tribool from_toml(const value& v)
        {
            tribool t;
            t.set(v.as_boolean());
            return t;
        }
    };
}

namespace INIBinding
{
    template<class T> struct from
    {};

    template<>
    struct from<ProxyGroupConfig>
    {
        static ProxyGroupConfigs from_ini(const StrArray &arr)
        {
            ProxyGroupConfigs confs;
            for(const String &x : arr)
            {
                unsigned int rules_upper_bound = 0;
                ProxyGroupConfig conf;

                StrArray vArray = split(x, "`");
                if(vArray.size() < 3)
                    continue;

                conf.Name = vArray[0];
                String type = vArray[1];

                rules_upper_bound = vArray.size();
                switch(hash_(type))
                {
                case "select"_hash:
                    conf.Type = ProxyGroupType::Select;
                    break;
                case "relay"_hash:
                    conf.Type = ProxyGroupType::Relay;
                    break;
                case "url-test"_hash:
                    conf.Type = ProxyGroupType::URLTest;
                    break;
                case "fallback"_hash:
                    conf.Type = ProxyGroupType::Fallback;
                    break;
                case "load-balance"_hash:
                    conf.Type = ProxyGroupType::LoadBalance;
                    break;
                case "ssid"_hash:
                    conf.Type = ProxyGroupType::SSID;
                    break;
                case "smart"_hash:
                    conf.Type = ProxyGroupType::Smart;
                    break;
                default:
                    continue;
                }

                if(conf.Type == ProxyGroupType::URLTest || conf.Type == ProxyGroupType::LoadBalance || conf.Type == ProxyGroupType::Fallback)
                {
                    if(rules_upper_bound < 5)
                        continue;
                    rules_upper_bound -= 2;
                    conf.Url = vArray[rules_upper_bound];
                    parseGroupTimes(vArray[rules_upper_bound + 1], &conf.Interval, &conf.Timeout, &conf.Tolerance);
                }
                else if(conf.Type == ProxyGroupType::Smart)
                {
                    if(rules_upper_bound < 4)
                        continue;
                    // For smart type, URL is at fixed position 3
                    conf.Url = vArray[3];
                    
                    // Check if position 4 is interval or smart parameter
                    if(vArray.size() > 4 && !vArray[4].empty() && isdigit(vArray[4][0]))
                    {
                        // Position 4 is interval
                        parseGroupTimes(vArray[4], &conf.Interval, &conf.Timeout, &conf.Tolerance);
                        rules_upper_bound = 3;
                        
                        // Parse additional smart parameters starting from position 5
                        if(vArray.size() > 5)
                        {
                            std::string uselightgbm_str = vArray[5];
                            if(uselightgbm_str.find("uselightgbm=true") != std::string::npos)
                                conf.UseLightGBM = true;
                            else if(uselightgbm_str.find("uselightgbm=false") != std::string::npos)
                                conf.UseLightGBM = false;
                            else
                                conf.UseLightGBM = true; // default true
                        }
                        
                        if(vArray.size() > 6)
                        {
                            std::string collectdata_str = vArray[6];
                            if(collectdata_str.find("collectdata=true") != std::string::npos)
                                conf.CollectData = true;
                            else if(collectdata_str.find("collectdata=false") != std::string::npos)
                                conf.CollectData = false;
                            else
                                conf.CollectData = false; // default false
                        }
                        
                        if(vArray.size() > 7)
                        {
                            std::string strategy_str = vArray[7];
                            if(strategy_str.find("strategy=round-robin") != std::string::npos)
                                conf.SmartStrategyType = SmartStrategy::RoundRobin;
                            else if(strategy_str.find("strategy=sticky-sessions") != std::string::npos)
                                conf.SmartStrategyType = SmartStrategy::StickySessions;
                            else
                                conf.SmartStrategyType = SmartStrategy::StickySessions; // default sticky-sessions
                        }
                        
                        if(vArray.size() > 8)
                        {
                            std::string policy_priority_str = vArray[8];
                            if(policy_priority_str.find("policy-priority=\"") != std::string::npos)
                            {
                                size_t start = policy_priority_str.find("\"") + 1;
                                size_t end = policy_priority_str.rfind("\"");
                                if(start != std::string::npos && end != std::string::npos && end > start)
                                {
                                    std::string value = policy_priority_str.substr(start, end - start);
                                    if(!value.empty()) {
                                        conf.PolicyPriority = value;
                                        conf.PolicyPrioritySet = true;
                                    }
                                }
                            }
                        }
                    }
                    else
                    {
                        // No interval specified, smart parameters start from position 4
                        rules_upper_bound = 3;
                        
                        // Parse additional smart parameters starting from position 4
                        if(vArray.size() > 4)
                        {
                            std::string uselightgbm_str = vArray[4];
                            if(uselightgbm_str.find("uselightgbm=true") != std::string::npos)
                                conf.UseLightGBM = true;
                            else if(uselightgbm_str.find("uselightgbm=false") != std::string::npos)
                                conf.UseLightGBM = false;
                            else
                                conf.UseLightGBM = true; // default true
                        }
                        
                        if(vArray.size() > 5)
                        {
                            std::string collectdata_str = vArray[5];
                            if(collectdata_str.find("collectdata=true") != std::string::npos)
                                conf.CollectData = true;
                            else if(collectdata_str.find("collectdata=false") != std::string::npos)
                                conf.CollectData = false;
                            else
                                conf.CollectData = false; // default false
                        }
                        
                        if(vArray.size() > 6)
                        {
                            std::string strategy_str = vArray[6];
                            if(strategy_str.find("strategy=round-robin") != std::string::npos)
                                conf.SmartStrategyType = SmartStrategy::RoundRobin;
                            else if(strategy_str.find("strategy=sticky-sessions") != std::string::npos)
                                conf.SmartStrategyType = SmartStrategy::StickySessions;
                            else
                                conf.SmartStrategyType = SmartStrategy::StickySessions; // default sticky-sessions
                        }
                        
                        if(vArray.size() > 7)
                        {
                            std::string policy_priority_str = vArray[7];
                            if(policy_priority_str.find("policy-priority=\"") != std::string::npos)
                            {
                                size_t start = policy_priority_str.find("\"") + 1;
                                size_t end = policy_priority_str.rfind("\"");
                                if(start != std::string::npos && end != std::string::npos && end > start)
                                {
                                    std::string value = policy_priority_str.substr(start, end - start);
                                    if(!value.empty()) {
                                        conf.PolicyPriority = value;
                                        conf.PolicyPrioritySet = true;
                                    }
                                }
                            }
                        }
                    }
                }

                for(unsigned int i = 2; i < rules_upper_bound; i++)
                {
                    if(startsWith(vArray[i], "!!PROVIDER="))
                    {
                        string_array list = split(vArray[i].substr(11), ",");
                        conf.UsingProvider.reserve(conf.UsingProvider.size() + list.size());
                        std::move(list.begin(), list.end(), std::back_inserter(conf.UsingProvider));
                    }
                    else
                        conf.Proxies.emplace_back(std::move(vArray[i]));
                }
                confs.emplace_back(std::move(conf));
            }
            return confs;
        }
    };

    template<>
    struct from<RulesetConfig>
    {
        static RulesetConfigs from_ini(const StrArray &arr)
        {
            /*
            static const std::map<std::string, RulesetType> RulesetTypes = {
                {"clash-domain:", RulesetType::ClashDomain},
                {"clash-ipcidr:", RulesetType::ClashIpCidr},
                {"clash-classic:", RulesetType::ClashClassic},
                {"quanx:", RulesetType::QuantumultX},
                {"surge:", RulesetType::SurgeRuleset}
            };
            */
            RulesetConfigs confs;
            for(const String &x : arr)
            {
                RulesetConfig conf;
                String::size_type pos = x.find(",");
                if(pos == String::npos)
                    continue;
                conf.Group = x.substr(0, pos);
                if(x.substr(pos + 1, 2) == "[]")
                {
                    conf.Url = x.substr(pos + 1);
                    //conf.Type = RulesetType::SurgeRuleset;
                    confs.emplace_back(std::move(conf));
                    continue;
                }
                String::size_type epos = x.rfind(",");
                if(pos != epos)
                {
                    conf.Interval = to_int(x.substr(epos + 1), 0);
                    conf.Url = x.substr(pos + 1, epos - pos - 1);
                }
                else
                    conf.Url = x.substr(pos + 1);
                confs.emplace_back(std::move(conf));
            }
            return confs;
        }
    };

    template<>
    struct from<CronTaskConfig>
    {
        static CronTaskConfigs from_ini(const StrArray &arr)
        {
            CronTaskConfigs confs;
            for(const String &x : arr)
            {
                CronTaskConfig conf;
                StrArray vArray = split(x, "`");
                if(vArray.size() < 3)
                    continue;
                conf.Name = vArray[0];
                conf.CronExp = vArray[1];
                conf.Path = vArray[2];
                if(vArray.size() > 3)
                    conf.Timeout = to_int(vArray[3], 0);
                confs.emplace_back(std::move(conf));
            }
            return confs;
        }
    };

    template<>
    struct from<RegexMatchConfig>
    {
        static RegexMatchConfigs from_ini(const StrArray &arr, const std::string &delimiter)
        {
            RegexMatchConfigs confs;
            for(const String &x : arr)
            {
                RegexMatchConfig conf;
                if(startsWith(x, "script:"))
                {
                    conf.Script = x.substr(7);
                    confs.emplace_back(std::move(conf));
                    continue;
                }
                String::size_type pos = x.rfind(delimiter);
                conf.Match = x.substr(0, pos);
                if(pos != String::npos && pos < x.size() - 1)
                    conf.Replace = x.substr(pos + 1);
                confs.emplace_back(std::move(conf));
            }
            return confs;
        }
    };
}

#endif // BINDING_H_INCLUDED
