#!/bin/bash
set -xe

apk add gcc g++ build-base linux-headers cmake make autoconf automake libtool
apk add mbedtls-dev mbedtls-static zlib-dev rapidjson-dev zlib-static pcre2-dev

git clone --no-checkout https://github.com/curl/curl
cd curl
git fetch --depth=1 origin 8cd1397d3c5c9b1526c8d74530266a7a9a22294b
git checkout 8cd1397d3c5c9b1526c8d74530266a7a9a22294b
cmake -DCURL_USE_MBEDTLS=ON -DHTTP_ONLY=ON -DBUILD_TESTING=OFF -DBUILD_SHARED_LIBS=OFF -DCMAKE_USE_LIBSSH2=OFF -DBUILD_CURL_EXE=OFF . > /dev/null
make install -j2 > /dev/null
cd ..

git clone --no-checkout https://github.com/jbeder/yaml-cpp
cd yaml-cpp
git fetch --depth=1 origin f7320141120f720aecc4c32be25586e7da9eb978
git checkout f7320141120f720aecc4c32be25586e7da9eb978
cmake -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_BUILD_TYPE=Release -DYAML_CPP_BUILD_TESTS=OFF -DYAML_CPP_BUILD_TOOLS=OFF . > /dev/null
make install -j3 > /dev/null
cd ..

git clone --no-checkout https://github.com/ftk/quickjspp.git
cd quickjspp
git fetch origin 0c00c48895919fc02da3f191a2da06addeb07f09
git checkout 0c00c48895919fc02da3f191a2da06addeb07f09
git submodule update --init --depth=1
cmake -DCMAKE_BUILD_TYPE=Release .
make quickjs -j3 > /dev/null
install -d /usr/lib/quickjs/
install -m644 quickjs/libquickjs.a /usr/lib/quickjs/
install -d /usr/include/quickjs/
install -m644 quickjs/quickjs.h quickjs/quickjs-libc.h /usr/include/quickjs/
install -m644 quickjspp.hpp /usr/include/
cd ..

git clone --no-checkout https://github.com/PerMalmberg/libcron
cd libcron
git fetch --depth=1 origin ee34810b11bd23c8be637345532f91059b68b2d7
git checkout ee34810b11bd23c8be637345532f91059b68b2d7
git submodule update --init --depth=1
cmake -DCMAKE_BUILD_TYPE=Release .
make libcron install -j3
cd ..

git clone --no-checkout https://github.com/ToruNiina/toml11
cd toml11
git fetch --depth=1 origin 499be3c177bcf9b42848d5d9567153e4edfcbc8a
git checkout 499be3c177bcf9b42848d5d9567153e4edfcbc8a
cmake -DCMAKE_CXX_STANDARD=11 .
make install -j4
cd ..

export PKG_CONFIG_PATH=/usr/lib64/pkgconfig
cmake -DCMAKE_BUILD_TYPE=Release \
      -DSUBCONVERTER_PROJECT_COMMIT="${SUBCONVERTER_PROJECT_COMMIT:-}" \
      -DSUBCONVERTER_BUILD_VERSION="${SUBCONVERTER_BUILD_VERSION:-}" .
make -j3
rm subconverter
# shellcheck disable=SC2046
g++ -o base/subconverter $(find CMakeFiles/subconverter.dir/src/ -name "*.o")  -static -lpcre2-8 -lyaml-cpp -L/usr/lib64 -lcurl -lmbedtls -lmbedcrypto -lmbedx509 -lz -l:quickjs/libquickjs.a -llibcron -O3 -s

cd base
chmod +rx subconverter
chmod +r ./*
cd ..
mv base subconverter
