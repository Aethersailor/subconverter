#!/bin/bash

# 自动生成版本号脚本
# 格式: v{tag}-{username}-{commit} 或 v{tag}-{commit} (如果无法获取用户名)

# 获取最新的 tag
LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")

# 获取当前 commit hash (短格式)
COMMIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

# 尝试获取GitHub仓库所有者用户名
# 方法1: 从远程URL获取
GITHUB_USERNAME=$(git remote get-url origin 2>/dev/null | sed -n 's/.*github\.com[:/]\([^/]*\)\/.*/\1/p')

# 方法2: 如果方法1失败，尝试从git config获取
if [ -z "$GITHUB_USERNAME" ]; then
    GITHUB_USERNAME=$(git config --get user.name 2>/dev/null)
fi

# 生成版本号
if [ -n "$GITHUB_USERNAME" ]; then
    VERSION="${LATEST_TAG}-${GITHUB_USERNAME}-${COMMIT_HASH}"
else
    VERSION="${LATEST_TAG}-${COMMIT_HASH}"
fi

# 更新 version.h 文件
cat > src/version.h << EOF
#ifndef VERSION_H_INCLUDED
#define VERSION_H_INCLUDED

#define VERSION "${VERSION}"

#endif // VERSION_H_INCLUDED
EOF

echo "Generated version: ${VERSION}" 