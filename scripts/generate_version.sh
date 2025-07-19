#!/bin/bash

# 自动生成版本号脚本
# 格式: v{tag}-asailor-{commit}

# 获取最新的 tag
LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")

# 获取当前 commit hash (短格式)
COMMIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")

# 生成版本号
VERSION="${LATEST_TAG}-asailor-${COMMIT_HASH}"

# 更新 version.h 文件
cat > src/version.h << EOF
#ifndef VERSION_H_INCLUDED
#define VERSION_H_INCLUDED

#define VERSION "${VERSION}"

#endif // VERSION_H_INCLUDED
EOF

echo "Generated version: ${VERSION}" 