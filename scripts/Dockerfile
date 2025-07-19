FROM alpine:3.19 AS builder
LABEL maintainer="official@asailor.org"
ARG THREADS="4"
ARG SHA=""
ARG BRANCH="master"

WORKDIR /

RUN apk add --no-cache --virtual .build-tools git g++ build-base linux-headers cmake python3 bash py3-pip
RUN apk add --no-cache --virtual .build-deps curl-dev rapidjson-dev pcre2-dev yaml-cpp-dev

RUN git clone https://github.com/ftk/quickjspp --depth=1
WORKDIR /quickjspp
RUN git submodule update --init
RUN cmake -DCMAKE_BUILD_TYPE=Release .
RUN make quickjs -j $THREADS
RUN install -d /usr/lib/quickjs/
RUN install -m644 quickjs/libquickjs.a /usr/lib/quickjs/
RUN install -d /usr/include/quickjs/
RUN install -m644 quickjs/quickjs.h quickjs/quickjs-libc.h /usr/include/quickjs/
RUN install -m644 quickjspp.hpp /usr/include

WORKDIR /
RUN git clone https://github.com/PerMalmberg/libcron --depth=1
WORKDIR /libcron
RUN git submodule update --init
RUN cmake -DCMAKE_BUILD_TYPE=Release .
RUN make libcron -j $THREADS
RUN install -m644 libcron/out/Release/liblibcron.a /usr/lib/
RUN install -d /usr/include/libcron/
RUN install -m644 libcron/include/libcron/* /usr/include/libcron/
RUN install -d /usr/include/date/
RUN install -m644 libcron/externals/date/include/date/* /usr/include/date/

WORKDIR /
RUN git clone https://github.com/ToruNiina/toml11 --branch="v4.3.0" --depth=1
WORKDIR /toml11
RUN cmake -DCMAKE_CXX_STANDARD=11 .
RUN make install -j $THREADS

WORKDIR /
RUN git clone https://github.com/Aethersailor/subconverter --branch=$BRANCH --depth=50 --tags
WORKDIR /subconverter

# 生成版本号，优先使用传入的 SHA 参数
RUN echo "Current git status:"; \
    git status --porcelain; \
    echo "Available tags:"; \
    git tag --list | tail -5; \
    echo "Trying to get latest tag..."; \
    # 尝试多种方式获取最新tag \
    echo "Trying git describe --tags --abbrev=0..."; \
    LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null); \
    if [ -z "$LATEST_TAG" ]; then \
        echo "git describe failed, trying git tag --sort=-version:refname..."; \
        LATEST_TAG=$(git tag --sort=-version:refname | head -1 2>/dev/null); \
    fi; \
    if [ -z "$LATEST_TAG" ]; then \
        echo "git tag failed, using fallback..."; \
        LATEST_TAG="v0.0.0"; \
    fi; \
    echo "Found latest tag: $LATEST_TAG"; \
    echo "All available tags:"; \
    git tag --list | tail -10; \
    # 获取当前 commit hash \
    COMMIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown"); \
    echo "Current commit hash: $COMMIT_HASH"; \
    # 如果传入了 SHA 参数，则使用它，否则使用当前 commit hash \
    if [ -n "$SHA" ]; then \
        echo "Using provided SHA: $SHA"; \
        VERSION="${LATEST_TAG}-asailor-${SHA}"; \
    else \
        echo "Using current commit hash: $COMMIT_HASH"; \
        VERSION="${LATEST_TAG}-asailor-${COMMIT_HASH}"; \
    fi; \
    echo "#ifndef VERSION_H_INCLUDED" > src/version.h; \
    echo "#define VERSION_H_INCLUDED" >> src/version.h; \
    echo "" >> src/version.h; \
    echo "#define VERSION \"${VERSION}\"" >> src/version.h; \
    echo "" >> src/version.h; \
    echo "#endif // VERSION_H_INCLUDED" >> src/version.h; \
    echo "Generated version: ${VERSION}"; \
    echo "Version file content:"; \
    cat src/version.h;
RUN python3 -m pip install --upgrade pip --break-system-packages
RUN python3 -m pip install gitpython --break-system-packages
RUN python3 scripts/update_rules.py -c scripts/rules_config.conf
RUN cmake -DCMAKE_BUILD_TYPE=Release .
RUN make -j $THREADS

# build final image
FROM alpine:3.19
LABEL maintainer="official@asailor.org"

RUN apk add --no-cache --virtual subconverter-deps pcre2 libcurl yaml-cpp

COPY --from=builder /subconverter/subconverter /usr/bin/
COPY --from=builder /subconverter/base /base/

ENV TZ=Africa/Abidjan
RUN ln -sf /usr/share/zoneinfo/$TZ /etc/localtime
RUN echo $TZ > /etc/timezone

# set entry
WORKDIR /base
CMD ["subconverter"]

EXPOSE 25500/tcp
