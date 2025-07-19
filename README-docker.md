# subconverter-docker

这是一个用于运行 [https://github.com/Aethersailor/subconverter](https://github.com/Aethersailor/subconverter) 的精简镜像。

## 如何运行

只需使用以下命令即可运行此 docker 镜像：

```bash
# 以后台方式运行容器，将内部端口 25500 映射到主机的 25500 端口
docker run -d --restart=always -p 25500:25500 aethersailor/subconverter:latest
# 然后检查其状态
curl http://localhost:25500/version
# 如果你看到 `subconverter vx.x.x backend`，说明容器已启动并运行
```

或者使用 docker-compose 运行：

```yaml
---
version: '3'
services:
  subconverter:
    image: aethersailor/subconverter:latest
    container_name: subconverter
    ports:
      - "15051:25500"
    restart: always
```

## 如何更新配置

如果你想在 docker 容器内更新 `pref` 配置，可以使用以下命令：

```bash
# 假设你的配置文件名为 `newpref.ini`
curl -F "data=@newpref.ini" http://localhost:25500/updateconf?type=form\&token=password
# 你可能需要在你的配置文件中更改这个 token
```

## 使用自定义配置和规则、片段、profiles

如果你想使用你自己的 `pref` 配置和/或规则、片段、profiles：

```dockerfile
# 你可以将想要替换的文件保存到一个文件夹中，然后复制到 docker 容器中
# 使用官方 docker 的最新构建
FROM aethersailor/subconverter:latest
# 假设你的文件都在 replacements/ 目录下
# subconverter 文件夹位于 /base/，其结构与仓库中的 base/ 目录相同
COPY replacements/ /base/
# 暴露内部端口
EXPOSE 25500
# 注意，启动 docker 时仍需使用 '-p 25500:25500' 来映射端口
```

将上述内容保存为 `Dockerfile`，然后运行：

```bash
# 用这个 Dockerfile 构建并打上 subconverter-custom 标签
docker build -t subconverter-custom:latest .
# 以后台方式运行容器，将内部端口 25500 映射到主机的 25500 端口
docker run -d --restart=always -p 25500:25500 subconverter-custom:latest
# 然后检查其状态
curl http://localhost:25500/version
# 如果你看到 `subconverter vx.x.x backend`，说明容器已启动并运行
```

---

如需根据实际情况调整端口、文件路径等参数，请自行修改。
