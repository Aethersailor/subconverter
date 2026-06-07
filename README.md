# subconverter anti-fingerprint edition

这是 [asdlokj1qpi233/subconverter](https://github.com/asdlokj1qpi233/subconverter) 的自动同步衍生版本，适合希望减少订阅转换请求特征、同时持续获得上游更新的用户。

本仓库只说明与上游不同的内容。安装、配置、支持格式和 API 用法请查看[上游 README](https://github.com/asdlokj1qpi233/subconverter#readme)。

## 当前版本

| 项目 | 当前值 |
| --- | --- |
| 上游分支 | `master` |
| 上游版本 | `v0.9.9` |
| 上游提交 | [`633ecd5a`](https://github.com/asdlokj1qpi233/subconverter/commit/633ecd5a3b33cf288658f0910fb2cc5faabd351c) |
| 上次成功同步 | `2026-06-07T11:08:49+08:00` |
| 当前 Mihomo User-Agent | `mihomo/1.19.27` |
| User-Agent 来源 | [MetaCubeX/mihomo 最新 Release](https://github.com/MetaCubeX/mihomo/releases/latest) |
| 本项目版本后缀 | `af` |

## 与上游的区别

- 出站订阅和规则请求默认使用自动跟踪的 Mihomo 最新版本 User-Agent。
- 不发送 `SubConverter-Request`、`SubConverter-Version` 和 CORS `X-Requested-With` 指纹头。
- GET 和 HEAD 请求不再默认发送 JSON `Content-Type`。
- 订阅入口的客户端 UA、Host 和代理链相关请求头不会透传到目标地址。
- 脚本化 HTTP 请求显式设置的 User-Agent 和 Content-Type 保持不变。
- Docker 镜像直接从当前仓库提交构建，不会在构建过程中克隆浮动分支。
- 提供带独立版本号的多平台二进制文件，以及 Docker Hub 和 GHCR 多架构镜像。

## 版本标识

程序版本沿用上游格式，并追加本项目标识和本项目提交：

```text
v<上游版本>-<上游提交>-af.<本项目提交>
```

例如：

```text
v0.9.9-633ecd5a-af.1a2b3c4d
```

前半部分用于定位上游版本和提交，`af` 后的提交来自本仓库。Mihomo User-Agent 版本不会进入程序版本、Git 标签、Release 名称、构件名称或 Docker 标签。

## Docker 镜像

```text
docker.io/aethersailor/subconverter:latest
ghcr.io/aethersailor/subconverter:latest
```

除 `latest` 外，每次发布还会提供不可变的完整版本标签和项目提交标签。容器启动后可通过以下接口确认实际运行版本：

```bash
curl http://127.0.0.1:25500/version
```

容器运行方式和配置文件说明与上游一致，请参考[上游文档](https://github.com/asdlokj1qpi233/subconverter#readme)。

## 自动更新

- 每日检查上游提交和 Mihomo 最新 Release，也支持手动触发。
- 没有变化时不会创建同步提交或重复发布。
- 候选代码、原生构件和镜像通过测试后，才会更新 `master` 和公开标签。
- 上游原始标签不会被移动，也不会被复制为缺少本项目后缀的发布标签。
