FROM alpine:3.22 AS builder

ARG THREADS=4
ARG PROJECT_COMMIT=local
ARG BUILD_VERSION=

WORKDIR /build

RUN set -eux; \
    apk add --no-cache \
        build-base \
        cmake \
        curl-dev \
        git \
        linux-headers \
        pcre2-dev \
        rapidjson-dev \
        yaml-cpp-dev; \
    git clone --no-checkout https://github.com/ftk/quickjspp.git /tmp/quickjspp; \
    git -C /tmp/quickjspp fetch --depth=1 origin 0c00c48895919fc02da3f191a2da06addeb07f09; \
    git -C /tmp/quickjspp checkout 0c00c48895919fc02da3f191a2da06addeb07f09; \
    git -C /tmp/quickjspp submodule update --init --depth=1; \
    cmake -S /tmp/quickjspp -B /tmp/quickjspp/build -DCMAKE_BUILD_TYPE=Release; \
    cmake --build /tmp/quickjspp/build --target quickjs --parallel "$THREADS"; \
    install -d /usr/lib/quickjs /usr/include/quickjs; \
    install -m644 /tmp/quickjspp/quickjs/libquickjs.a /usr/lib/quickjs/; \
    install -m644 /tmp/quickjspp/quickjs/quickjs.h /tmp/quickjspp/quickjs/quickjs-libc.h /usr/include/quickjs/; \
    install -m644 /tmp/quickjspp/quickjspp.hpp /usr/include/; \
    git clone --no-checkout https://github.com/PerMalmberg/libcron.git /tmp/libcron; \
    git -C /tmp/libcron fetch --depth=1 origin ee34810b11bd23c8be637345532f91059b68b2d7; \
    git -C /tmp/libcron checkout ee34810b11bd23c8be637345532f91059b68b2d7; \
    git -C /tmp/libcron submodule update --init --depth=1; \
    cmake -S /tmp/libcron -B /tmp/libcron/build -DCMAKE_BUILD_TYPE=Release; \
    cmake --build /tmp/libcron/build --target libcron --parallel "$THREADS"; \
    install -m644 /tmp/libcron/libcron/out/Release/liblibcron.a /usr/lib/; \
    install -d /usr/include/libcron /usr/include/date; \
    install -m644 /tmp/libcron/libcron/include/libcron/* /usr/include/libcron/; \
    install -m644 /tmp/libcron/libcron/externals/date/include/date/* /usr/include/date/; \
    git clone --no-checkout https://github.com/ToruNiina/toml11.git /tmp/toml11; \
    git -C /tmp/toml11 fetch --depth=1 origin 499be3c177bcf9b42848d5d9567153e4edfcbc8a; \
    git -C /tmp/toml11 checkout 499be3c177bcf9b42848d5d9567153e4edfcbc8a; \
    cmake -S /tmp/toml11 -B /tmp/toml11/build -DCMAKE_CXX_STANDARD=11; \
    cmake --build /tmp/toml11/build --parallel "$THREADS"; \
    cmake --install /tmp/toml11/build

COPY . /src

RUN set -eux; \
    cmake -S /src -B /build/subconverter \
        -DCMAKE_BUILD_TYPE=Release \
        -DSUBCONVERTER_PROJECT_COMMIT="$PROJECT_COMMIT" \
        -DSUBCONVERTER_BUILD_VERSION="$BUILD_VERSION"; \
    cmake --build /build/subconverter --parallel "$THREADS"

FROM alpine:3.22

ARG BUILD_VERSION
ARG PROJECT_COMMIT
ARG UPSTREAM_COMMIT

LABEL org.opencontainers.image.title="subconverter anti-fingerprint edition" \
      org.opencontainers.image.source="https://github.com/Aethersailor/subconverter" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${PROJECT_COMMIT}" \
      io.aethersailor.subconverter.upstream-revision="${UPSTREAM_COMMIT}"

RUN apk add --no-cache curl libstdc++ pcre2 yaml-cpp

COPY --from=builder /build/subconverter/subconverter /usr/bin/subconverter
COPY --from=builder /src/base /base

ENV TZ=Etc/UTC
WORKDIR /base

EXPOSE 25500/tcp
CMD ["subconverter"]
