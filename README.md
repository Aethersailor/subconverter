<!--
本文件由 scripts/project_metadata.py 根据
.github/templates/README.md.tmpl、.github/project-metadata.json 和
.github/source-lock.json 自动生成。请修改模板，不要直接编辑 README.md。
-->

# subconverter anti-fingerprint edition

这是 [asdlokj1qpi233/subconverter](https://github.com/asdlokj1qpi233/subconverter) 的自动同步衍生版本：保留上游的订阅转换能力，只把“访问机场订阅服务商”的 `SubscriptionProvider` 获取链路替换为与锁定 Mihomo Release 同源、可校验、失败时拒绝降级的 companion helper。

> [!IMPORTANT]
> 这里的目标不是只替换一个 User-Agent 字符串，而是最大化对齐机场服务商能够观察到的 Mihomo Provider 请求行为。subconverter 对外提供的 API、普通规则下载和模板请求仍然保持各自原有身份。

## 快速开始

以下命令只把服务绑定到本机回环地址，适合先确认镜像和版本：

```bash
docker run -d \
  --name subconverter \
  --restart unless-stopped \
  -p 127.0.0.1:25500:25500 \
  docker.io/aethersailor/subconverter:latest

curl --fail http://127.0.0.1:25500/version
```

也可以使用 `ghcr.io/aethersailor/subconverter:latest`。正式部署时的配置持久化、端口暴露和 API 用法与上游一致，请先阅读[上游 README](https://github.com/asdlokj1qpi233/subconverter#readme)；反向代理和日志还应遵循本仓库的[隐私部署边界](docs/PRIVACY.md)。

原生二进制发布覆盖 Linux、macOS 和 Windows，下载入口见本仓库的 [Releases](https://github.com/Aethersailor/subconverter/releases)。

## 它改变了什么

| 请求边界 | 实际行为 |
| --- | --- |
| 机场订阅获取 | 只有调用链显式标记为 `SubscriptionProvider` 的请求交给 companion helper；helper 从下表锁定的 Mihomo 官方 tag/commit 源码构建，并复用 Mihomo 的 Provider 获取实现。 |
| 普通出站请求 | Generic 出站请求（规则、模板等）继续使用上游的 `subconverter/<version> cURL/<libcurl-version>` 身份。 |
| 入站请求头 | 客户端、CDN 和反向代理请求头不会穿透到机场订阅地址；旧的 `SubConverter-Request`、`SubConverter-Version` 和 CORS `X-Requested-With` 指纹头不会发送。 |
| 请求方法与内容类型 | GET 和 HEAD 不再默认携带 JSON `Content-Type`；脚本显式设置的 User-Agent 和 Content-Type 仍保持不变。 |
| 身份不匹配 | helper、manifest、源码锁或平台不一致时失败关闭，不会悄悄退回普通 cURL 或 DIRECT 实现。 |

## Mihomo 对齐范围

companion helper 不是重新实现一套“看起来像 Mihomo”的 HTTP 客户端。它作为 overlay 编译进锁定的 Mihomo 源码树，并使用 Mihomo 的：

- `HTTPVehicle`、inner tunnel、官方 outbound adapter 和 proxy dialer；
- Provider 超时、ETag 状态、重定向、原始响应体和重复响应头处理；
- 与锁定官方发布物一致的嵌入式 CA；
- 默认 Provider User-Agent、HTTP/1.1 请求顺序和 TLS ClientHello 行为；
- DIRECT、HTTP、HTTPS、SOCKS5 和 SOCKS5H 代理路径。

Linux amd64 发布构建会把 helper 与锁定的官方 Mihomo 可执行文件进行 Oracle 对照：完整比较 HTTP/1.1 请求和解析后的 TLS ClientHello，只归一化动态端口、随机数、会话 ID 与临时密钥等逐连接不确定数据。缺少观测层、出现未知字段或对照不一致都会使构建失败。

本项目只对上述可测试的 Provider 获取边界声明一致性。服务端 IP、网络路径、请求时机、订阅内容、外层 CDN/反向代理和它们的日志策略不属于 Mihomo helper 能隐藏的范围。

实现细节见：

- [helper IPC 与安全协议](mihomo-fetcher/PROTOCOL.md)
- [锁定构建与多平台打包契约](scripts/MIHOMO_FETCHER_PACKAGING.md)
- [一致性捕获、归一化和差异规则](scripts/mihomo_conformance/README.md)

## 当前锁定身份

| 项目 | 当前值 |
| --- | --- |
| 上游仓库与分支 | [`asdlokj1qpi233/subconverter`](https://github.com/asdlokj1qpi233/subconverter) / `master` |
| 上游版本 | `v0.9.9` |
| 上游提交 | [`6e94f496`](https://github.com/asdlokj1qpi233/subconverter/commit/6e94f496d1e170282321119214de08e3826fa24f) |
| 上游源码同步时间 | `2026-07-10T06:25:11+00:00` |
| 锁定的 Mihomo Release | [`v1.19.29`](https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.29) |
| 锁定的 Mihomo 提交 | [`e26714a1`](https://github.com/MetaCubeX/mihomo/commit/e26714a181ac0e2fa803453c0a8e9a9ce94e31cb) |
| 源码配对 ID | `sha256:44fb36d28ffb0d4c228f23b51e038ee592a44fb66ac5a6058b41703f478a3444` |
| 模仿契约 | `mihomo-provider-fetch-v1`（helper protocol `1`） |
| 本项目版本后缀 | `af` |

`.github/source-lock.json` 是上述 Mihomo 身份、官方发布资产、上游源码与项目 overlay 的唯一锁文件。每个平台发布包还包含 `subconverter-mihomo-fetcher.manifest.json`，用于绑定 helper SHA-256、目标平台、Mihomo commit、overlay hash 和源码配对 ID。

容器内可以直接核对运行时 helper：

```bash
docker exec subconverter sha256sum /usr/bin/subconverter-mihomo-fetcher
docker exec subconverter cat /usr/share/subconverter/subconverter-mihomo-fetcher.manifest.json
```

## 版本与镜像标签

程序版本沿用上游格式，并追加本项目标识和当前仓库提交：

```text
v<上游版本>-<上游提交>-af.<本项目提交>
```

例如：

```text
v0.9.9-633ecd5a-af.1a2b3c4d
```

前半部分定位上游源码，`af` 后定位本仓库源码。Mihomo helper 使用独立的源码锁和配对 ID，不混入 subconverter 程序版本。

公开镜像：

```text
docker.io/aethersailor/subconverter:latest
ghcr.io/aethersailor/subconverter:latest
```

自动发布还会生成完整版本标签、`sha-<本项目提交>` 和 `edge`。`stable` 只在正式 Release 路径更新。严谨部署应优先记录并使用 OCI digest，同时核对镜像的 `org.opencontainers.image.revision` 与 `/version`。

## 自动同步与发布

定时任务和手动任务共用同一条失败关闭流水线：

1. 解析上游 `master` 和 Mihomo 最新稳定 Release 的真实 commit、tree、签名状态及官方资产摘要。
2. 生成完整 `.github/source-lock.json` 和新的源码配对 ID；README 中不维护第二份 Mihomo 版本号。
3. 为八个原生目标构建 helper，验证平台和 manifest，并对 Linux amd64 运行官方 Mihomo Oracle 对照。
4. 构建原生包与 Docker 候选镜像，执行真实 `SubscriptionProvider` 出站测试。
5. 所有候选检查通过后才更新 `master`、Docker Hub、GHCR 和相应公开标签。

定时检查没有发现变化时，不创建同步提交，也不重复发布。上游原始标签不会被移动或伪装成本项目发布标签。

## 隐私和运维边界

- 应用日志会把 `/sub` 入站请求截断为 path，只记录 header 名称；机场订阅目标以 `[redacted]` 表示。
- helper 不通过命令行参数、环境变量或非结构化日志传递订阅 URL、凭据、请求头和正文。
- 反向代理、CDN、WAF 和宿主机访问日志不受应用日志脱敏控制。若日志格式包含完整 `$request`、`$request_uri` 或 `$args`，查询串中的订阅 URL/Token 仍可能落盘。
- helper 数据目录保存 Mihomo ETag 状态，应按敏感数据保护；高隐私部署可把 `SUBCONVERTER_MIHOMO_DATA_DIR` 指向权限受限的 tmpfs。
- `/base/cache` 可能保存订阅响应内容；是否持久化应由部署者按实际隐私要求决定。

可直接使用的 OpenResty/Nginx 日志建议、tmpfs 示例和历史日志处理边界见 [docs/PRIVACY.md](docs/PRIVACY.md)。

## README 如何维护

本文件是生成物。请修改 `.github/templates/README.md.tmpl`，不要直接编辑 `README.md`。

生成并检查：

```bash
python3 scripts/project_metadata.py render
python3 scripts/project_metadata.py render --check
python3 -m unittest discover -s tests -p 'test_project_customization.py' -v
```

专用文档工作流会在 README、模板、元数据、源码锁或关联文档变化时立即验证生成结果和本地链接；纯文档提交不会触发原生八平台或 Docker 多架构重编译。
