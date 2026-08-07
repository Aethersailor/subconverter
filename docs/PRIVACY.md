# 隐私部署边界

subconverter anti-fingerprint edition 只负责应用内部的请求隔离、日志脱敏和 Mihomo Provider 获取链路。反向代理、CDN、WAF、容器运行时和宿主机日志属于独立的数据边界，必须由部署者单独配置。

## 反向代理访问日志

`/sub` 通常通过查询参数携带订阅地址；如果访问日志记录 `$request`、`$request_uri`、`$args` 或 `$query_string`，机场 URL 及其 Token 可能直接写入日志。

建议为该站点使用只记录 path、不记录参数和敏感 header 值的专用格式，例如：

```nginx
log_format subconverter_privacy
    '$remote_addr [$time_local] '
    '"$request_method $uri $server_protocol" '
    '$status $body_bytes_sent '
    'rt=$request_time us=$upstream_status urt=$upstream_response_time';

access_log /path/to/subconverter-access.log subconverter_privacy;
```

日志格式中不应出现：

- `$request`、`$request_uri`、`$args`、`$query_string`；
- `$http_authorization` 或任意可能承载凭据的 `$http_*`；
- 不必要的 Referer，因为它也可能携带完整查询串。

修改后应先运行 `nginx -t` 或 `openresty -t`，确认配置有效后再 reload。错误日志也应单独审计：上游错误、WAF 命中记录和调试日志仍可能保存完整 URI。

## CDN 和 WAF

CDN、安全网关和 1Panel 等管理面可能在应用之前记录请求。应用内出现 `[redacted]` 不能证明这些外层系统没有保存查询参数。

部署后至少检查：

- CDN 请求日志、Bot/WAF 事件和调试追踪；
- WAF 数据库或审计记录中的 URI、匹配值与原始 Nginx 日志字段；
- 日志平台、告警通知和错误采集系统是否复制了完整请求。

若历史日志已经包含机场订阅 Token，应先限制日志访问权限，再轮换相应机场凭据；仅删除日志不能使已暴露的 Token 重新安全。

## helper ETag 状态

Mihomo helper 默认在系统缓存目录下维护 Provider ETag 状态。该目录权限会被收紧到 `0700`，但仍应视作敏感运行数据。

Docker Compose 可以把它放入 tmpfs：

```yaml
services:
  subconverter:
    environment:
      SUBCONVERTER_MIHOMO_DATA_DIR: /run/subconverter-mihomo
    tmpfs:
      - /run/subconverter-mihomo:mode=0700
```

tmpfs 会在容器重建后清空 ETag 状态，隐私更强，但可能增加机场订阅的重复下载。需要持久化时，应使用权限受限、纳入备份和日志审计范围的专用目录。

## subconverter 内容缓存

`/base/cache` 可能保存订阅响应和转换缓存。若机场订阅正文也不能落盘，可以关闭相应缓存，或把该目录放入 tmpfs。这样会牺牲缓存命中和故障时使用旧内容的能力，应按部署场景选择。

## 验收原则

生产验收不应把真实机场 URL 或 Token 直接写入 shell 参数、临时文件、CI 日志或聊天记录。推荐做法是：

1. 用合成订阅源验证完整 `/sub` 调用链和 Mihomo 出站指纹。
2. 从运行时核对镜像 digest、OCI revision、helper SHA-256 与 manifest。
3. 分别检查应用、反向代理、WAF 和外部日志平台的新日志增量。
4. 只有在所有日志边界都安全后，才使用真实机场凭据做必要的生产验收。
