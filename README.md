# subconverter

Utility to convert between various proxy subscription formats.

[![Build Status](https://github.com/Aethersailor/subconverter/actions/workflows/docker.yml/badge.svg)](https://github.com/Aethersailor/subconverter/actions)
[![Build Status](https://github.com/Aethersailor/subconverter/actions/workflows/build.yml/badge.svg)](https://github.com/Aethersailor/subconverter/actions)
[![GitHub tag (latest SemVer)](https://img.shields.io/github/tag/Aethersailor/subconverter.svg)](https://github.com/Aethersailor/subconverter/tags)
[![GitHub release](https://img.shields.io/github/release/Aethersailor/subconverter.svg)](https://github.com/Aethersailor/subconverter/releases)
[![GitHub license](https://img.shields.io/github/license/Aethersailor/subconverter.svg)](https://github.com/Aethersailor/subconverter/blob/master/LICENSE)

## 新增内容

2025/7/12

-   增加支持 Smart 策略组，以及自定义参数

范例：
```
custom_proxy_group=香港节点`smart`(港|HK|hk|Hong Kong|HongKong|hongkong|深港)`https://cp.cloudflare.com/generate_204`300`uselightgbm=true`collectdata=false`strategy=sticky-sessions`policy-priority="HK:1.1;SG:1.2"
```
Smart 策略组相关参数，均非必填项，已内置预设默认值（policy-priority 预设为空）。
 

<details>
<summary><b>更新历史</b></summary>
2025/7/12
-   创建分支
-   更换部分外部链接

2020/12/9

-   新增 [特别用法](#特别用法) 中 [规则转换](#规则转换) 的说明
-   修改 [配置文件](#配置文件) 中的 `clash_proxy_group` 为 `proxy_group` ，并增加修改描述与示例
-   修改 [配置文件](#配置文件) 中 `[ruleset]` 部分的 `surge_ruleset` 为 `ruleset ` ，并增加修改示例
-   修改 [外部配置](#外部配置) 中 `surge_ruleset` 为 `ruleset ` 
-   新增 [外部配置](#外部配置) 中 `add_emoji` 和 `remove_old_emoji` 
-   修改 [外部配置](#外部配置) 中 `proxy_group` 和  `ruleset ` 的描述与示例
-   调整 [简易用法](#简易用法) 与 [进阶用法](#进阶用法) 中的部分描述
-   更换文档中失效的外部链接

2020/11/20

-   新增 [支持类型](#支持类型) 中 `mixed` & `auto` 参数
-   新增 [进阶链接](#进阶链接) 中多个调用参数的说明
-   新增 [配置文件](#配置文件) 中 `[userinfo]` 部分的描述
-   新增 [配置文件](#配置文件) 中 `[common]`&`[node_pref]`&`[server]` 中多个参数的描述
-   修改 [进阶链接](#进阶链接) 中 `url` 参数的说明

2020/04/29

-   新增 [配置文件](#配置文件) 指定默认外部配置文件
-   新增 [配置文件](#配置文件) 中 `[aliases]` 参数的描述
-   新增 [模板功能](#模板功能) 用于直接渲染的 `/render` 接口的描述
-   修改 [支持类型](#支持类型) 中类 TG 类型节点的描述
-   调整 模板介绍 为 [模板功能](#模板功能)

2020/04/04

-   新增 [模板介绍](#模板介绍) 用于对所引用的 `base` 基础模板进行高度个性化自定义
-   新增 [配置文件](#配置文件) 中 `[template]` 参数的描述
-   新增 [外部配置](#外部配置) 中 `[template]` 参数的描述
-   新增 [本地生成](#本地生成) 用于在本地生成具体的配置文件
-   新增 [支持类型](#支持类型) 中 `mellow` & `trojan` 参数
-   新增 [进阶链接](#进阶链接) 中 `new_name` 参数的描述
-   新增 [配置文件](#配置文件) 中 `append_sub_userinfo` `clash_use_new_field_name` 参数的描述
-   调整 [说明目录](#说明目录) 层次

2020/03/02

-   新增 [进阶链接](#进阶链接) 中关于 `append_type` `append_info` `expand` `dev_id` `interval` `strict` 等参数的描述

</details>


[Docker README](https://github.com/Aethersailor/subconverter/blob/master/README-docker.md)

[中文文档](https://github.com/Aethersailor/subconverter/blob/master/README-cn.md)

- [subconverter](#subconverter)
  - [Supported Types](#supported-types)
  - [Quick Usage](#quick-usage)
    - [Access Interface](#access-interface)
    - [Description](#description)
  - [Advanced Usage](#advanced-usage)
  - [Auto Upload](#auto-upload)

## Supported Types

| Type         | As Source  | As Target    | Target Name |
| ------------ | :--------: | :----------: | ----------- |
| Clash        |     ✓      |      ✓       | clash       |
| ClashR       |     ✓      |      ✓       | clashr      |
| Quantumult   |     ✓      |      ✓       | quan        |
| Quantumult X |     ✓      |      ✓       | quanx       |
| Loon         |     ✓      |      ✓       | loon        |
| SS (SIP002)  |     ✓      |      ✓       | ss          |
| SS Android   |     ✓      |      ✓       | sssub       |
| SSD          |     ✓      |      ✓       | ssd         |
| SSR          |     ✓      |      ✓       | ssr         |
| Surfboard    |     ✓      |      ✓       | surfboard   |
| Surge 2      |     ✓      |      ✓       | surge&ver=2 |
| Surge 3      |     ✓      |      ✓       | surge&ver=3 |
| Surge 4      |     ✓      |      ✓       | surge&ver=4 |
| V2Ray        |     ✓      |      ✓       | v2ray       |
| Telegram-liked HTTP/Socks 5 links |     ✓      |      ×       | Only as source |

Notice:

1. Shadowrocket users should use `ss`, `ssr` or `v2ray` as target.

2. You can add `&remark=` to Telegram-liked HTTP/Socks 5 links to set a remark for this node. For example:

   - tg://http?server=1.2.3.4&port=233&user=user&pass=pass&remark=Example

   - https://t.me/http?server=1.2.3.4&port=233&user=user&pass=pass&remark=Example


---

## Quick Usage

> Using default groups and rulesets configuration directly, without changing any settings

### Access Interface

```txt
http://127.0.0.1:25500/sub?target=%TARGET%&url=%URL%&config=%CONFIG%
```

### Description

| Argument | Required | Example | Description |
| -------- | :------: | :------ | ----------- |
| target   | Yes      | clash   | Target subscription type. Acquire from Target Name in [Supported Types](#supported-types). |
| url      | Yes      | https%3A%2F%2Fwww.xxx.com | Subscription to convert. Supports URLs and file paths. Process with [URLEncode](https://www.urlencoder.org/) first. |
| config   | No       | https%3A%2F%2Fwww.xxx.com | External configuration file path. Supports URLs and file paths. Process with [URLEncode](https://www.urlencoder.org/) first. More examples can be found in [this](https://github.com/lzdnico/subconverteriniexample) repository. |

If you need to merge two or more subscription, you should join them with '|' before the URLEncode process.

Example:

```txt
You have 2 subscriptions and you want to merge them and generate a Clash subscription:
1. https://dler.cloud/subscribe/ABCDE?clash=vmess
2. https://rich.cloud/subscribe/ABCDE?clash=vmess

First use '|' to separate 2 subscriptions:
https://dler.cloud/subscribe/ABCDE?clash=vmess|https://rich.cloud/subscribe/ABCDE?clash=vmess

Then process it with URLEncode to get %URL%:
https%3A%2F%2Fdler.cloud%2Fsubscribe%2FABCDE%3Fclash%3Dvmess%7Chttps%3A%2F%2Frich.cloud%2Fsubscribe%2FABCDE%3Fclash%3Dvmess

Then fill %TARGET% and %URL% in Access Interface with actual values:
http://127.0.0.1:25500/sub?target=clash&url=https%3A%2F%2Fdler.cloud%2Fsubscribe%2FABCDE%3Fclash%3Dvmess%7Chttps%3A%2F%2Frich.cloud%2Fsubscribe%2FABCDE%3Fclash%3Dvmess

Finally subscribe this link in Clash and you are done!
```

---

## Advanced Usage

Please refer to [中文文档](https://github.com/Aethersailor/subconverter/blob/master/README-cn.md#%E8%BF%9B%E9%98%B6%E7%94%A8%E6%B3%95).

## Auto Upload

> Upload Gist automatically

Add a [Personal Access Token](https://github.com/settings/tokens/new) into [gistconf.ini](./gistconf.ini) in the root directory, then add `&upload=true` to the local subscription link, then when you access this link, the program will automatically update the content to Gist repository.

Example:

```ini
[common]
;uncomment the following line and enter your token to enable upload function
token = xxxxxxxxxxxxxxxxxxxxxxxx(Your Personal Access Token)
```
