#ifndef PROXY_H_INCLUDED
#define PROXY_H_INCLUDED

#include <string>
#include <vector>

#include "utils/tribool.h"

using String = std::string;
using StringArray = std::vector<String>;

enum class ProxyType
{
    Unknown,
    Shadowsocks,
    ShadowsocksR,
    VMess,
    VLESS,
    Trojan,
    Snell,
    HTTP,
    HTTPS,
    SOCKS5,
    WireGuard,
    Hysteria,
    Hysteria2,
    TUIC,
    AnyTLS
};

inline String getProxyTypeName(ProxyType type)
{
    switch(type)
    {
    case ProxyType::Shadowsocks:
        return "SS";
    case ProxyType::ShadowsocksR:
        return "SSR";
    case ProxyType::VMess:
        return "VMess";
    case ProxyType::VLESS:
        return "VLESS";
    case ProxyType::Trojan:
        return "Trojan";
    case ProxyType::Snell:
        return "Snell";
    case ProxyType::HTTP:
        return "HTTP";
    case ProxyType::HTTPS:
        return "HTTPS";
    case ProxyType::SOCKS5:
        return "SOCKS5";
    case ProxyType::WireGuard:
        return "WireGuard";
    case ProxyType::Hysteria:
        return "Hysteria";
    case ProxyType::Hysteria2:
        return "Hysteria2";
    case ProxyType::TUIC:
        return "TUIC";
    case ProxyType::AnyTLS:
        return "AnyTLS";
    default:
        return "Unknown";
    }
}

struct Proxy
{
    ProxyType Type = ProxyType::Unknown;
    uint32_t Id = 0;
    uint32_t GroupId = 0;
    String Group;
    String Remark;
    String Hostname;
    uint16_t Port = 0;

    String Username;
    String Password;
    String EncryptMethod;
    String Plugin;
    String PluginOption;
    String Protocol;
    String ProtocolParam;
    String OBFS;
    String OBFSParam;
    String UserId;
    uint16_t AlterId = 0;
    String TransferProtocol;
    String FakeType;
    bool TLSSecure = false;

    String Host;
    String Path;
    String Edge;

    String QUICSecure;
    String QUICSecret;

    tribool UDP;
    tribool TCPFastOpen;
    tribool AllowInsecure;
    tribool TLS13;

    String UnderlyingProxy;

    uint16_t SnellVersion = 0;
    String ServerName;

    String SelfIP;
    String SelfIPv6;
    String PublicKey;
    String PrivateKey;
    String PreSharedKey;
    StringArray DnsServers;
    uint16_t Mtu = 0;
    String AllowedIPs = "0.0.0.0/0, ::/0";
    uint16_t KeepAlive = 0;
    String TestUrl;
    String ClientId;

    String Ports;
    String Up;
    uint32_t UpSpeed = 0;
    String Down;
    uint32_t DownSpeed = 0;
    String AuthStr;
    String SNI;
    String Fingerprint;
    String Ca;
    String CaStr;
    uint32_t RecvWindowConn;
    uint32_t RecvWindow;
    tribool DisableMtuDiscovery;
    uint32_t HopInterval;
    StringArray Alpn;

    uint32_t CWND = 0;

    String UUID;
    String IP;
    String HeartbeatInterval;
    tribool DisableSNI;
    tribool ReduceRTT;
    uint32_t RequestTimeout;
    String UdpRelayMode;
    String CongestionController;
    uint32_t MaxUdpRelayPacketSize;
    tribool FastOpen;
    uint32_t MaxOpenStreams;

    uint32_t IdleSessionCheckInterval;
    uint32_t IdleSessionTimeout;
    uint32_t MinIdleSession;

    String Flow;
    uint32_t XTLS;
    String PacketEncoding;
    String ShortID;

    // VLESS specific fields
    String Network;  // tcp, ws, grpc
    String ClientFingerprint;  // chrome, firefox, safari, random, none
    String EchConfig;  // ECH configuration
    tribool SupportX25519Mlkem768;  // Reality support-x25519mlkem768
    String GrpcServiceName;  // gRPC service name
    String WsPath;  // WebSocket path
    String WsHeaders;  // WebSocket headers
    tribool V2rayHttpUpgrade;  // v2ray-http-upgrade
    tribool V2rayHttpUpgradeFastOpen;  // v2ray-http-upgrade-fast-open

    // Hysteria2 ECH specific fields
    tribool EchEnable;  // ECH enable flag
    
    // Hysteria2 quic-go specific fields
    uint32_t InitialStreamReceiveWindow = 0;
    uint32_t MaxStreamReceiveWindow = 0;
    uint32_t InitialConnectionReceiveWindow = 0;
    uint32_t MaxConnectionReceiveWindow = 0;

};

#define SS_DEFAULT_GROUP "SSProvider"
#define SSR_DEFAULT_GROUP "SSRProvider"
#define V2RAY_DEFAULT_GROUP "V2RayProvider"
#define VLESS_DEFAULT_GROUP "VLESSProvider"
#define SOCKS_DEFAULT_GROUP "SocksProvider"
#define HTTP_DEFAULT_GROUP "HTTPProvider"
#define TROJAN_DEFAULT_GROUP "TrojanProvider"
#define SNELL_DEFAULT_GROUP "SnellProvider"
#define WG_DEFAULT_GROUP "WireGuardProvider"
#define HYSTERIA_DEFAULT_GROUP "HysteriaProvider"
#define HYSTERIA2_DEFAULT_GROUP "Hysteria2Provider"
#define TUIC_DEFAULT_GROUP "TUICProvider"
#define ANYTLS_DEFAULT_GROUP "AnyTLSProvider"

#endif // PROXY_H_INCLUDED
