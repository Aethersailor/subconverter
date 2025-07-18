#ifndef PROXYGROUP_H_INCLUDED
#define PROXYGROUP_H_INCLUDED

#include "def.h"

enum class ProxyGroupType
{
    Select,
    URLTest,
    Fallback,
    LoadBalance,
    Relay,
    SSID,
    Smart
};

enum class BalanceStrategy
{
    ConsistentHashing,
    RoundRobin
};

enum class SmartStrategy
{
    StickySessions,
    RoundRobin
};

struct ProxyGroupConfig
{
    String Name;
    ProxyGroupType Type;
    StrArray Proxies;
    StrArray UsingProvider;
    String Url;
    Integer Interval = 0;
    Integer Timeout = 0;
    Integer Tolerance = 0;
    BalanceStrategy Strategy = BalanceStrategy::ConsistentHashing;
    SmartStrategy SmartStrategyType = SmartStrategy::StickySessions;
    String PolicyPriority;
    Boolean PolicyPrioritySet = false;
    Boolean Lazy;
    Boolean DisableUdp;
    Boolean Persistent;
    Boolean EvaluateBeforeUse;
    Boolean UseLightGBM = true;
    Boolean CollectData = false;

    String TypeStr() const
    {
        switch(Type)
        {
            case ProxyGroupType::Select: return "select";
            case ProxyGroupType::URLTest: return "url-test";
            case ProxyGroupType::LoadBalance: return "load-balance";
            case ProxyGroupType::Fallback: return "fallback";
            case ProxyGroupType::Relay: return "relay";
            case ProxyGroupType::SSID: return "ssid";
            case ProxyGroupType::Smart: return "smart";
        }
        return "";
    }

    String StrategyStr() const
    {
        switch(Strategy)
        {
            case BalanceStrategy::ConsistentHashing: return "consistent-hashing";
            case BalanceStrategy::RoundRobin: return "round-robin";
        }
        return "";
    }

    String SmartStrategyStr() const
    {
        switch(SmartStrategyType)
        {
            case SmartStrategy::StickySessions: return "sticky-sessions";
            case SmartStrategy::RoundRobin: return "round-robin";
        }
        return "";
    }
};

using ProxyGroupConfigs = std::vector<ProxyGroupConfig>;

#endif // PROXYGROUP_H_INCLUDED
