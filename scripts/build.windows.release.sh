#!/bin/bash
set -xe

git clone --no-checkout https://github.com/curl/curl
cd curl
git fetch --depth=1 origin 8cd1397d3c5c9b1526c8d74530266a7a9a22294b
git checkout 8cd1397d3c5c9b1526c8d74530266a7a9a22294b
cmake -DCMAKE_BUILD_TYPE=Release -DCURL_USE_LIBSSH2=OFF -DHTTP_ONLY=ON -DCURL_USE_SCHANNEL=ON -DBUILD_SHARED_LIBS=OFF -DBUILD_CURL_EXE=OFF -DCMAKE_INSTALL_PREFIX="$MINGW_PREFIX" -G "Unix Makefiles" -DHAVE_LIBIDN2=OFF -DCURL_USE_LIBPSL=OFF .
make install -j4
cd ..

git clone --no-checkout https://github.com/jbeder/yaml-cpp
cd yaml-cpp
git fetch --depth=1 origin f7320141120f720aecc4c32be25586e7da9eb978
git checkout f7320141120f720aecc4c32be25586e7da9eb978
sed -i '1i#include <cstdint>' src/emitterutils.cpp
cmake -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_BUILD_TYPE=Release -DYAML_CPP_BUILD_TESTS=OFF -DYAML_CPP_BUILD_TOOLS=OFF -DCMAKE_INSTALL_PREFIX="$MINGW_PREFIX" -G "Unix Makefiles" .
make install -j4
cd ..

git clone --no-checkout https://github.com/ftk/quickjspp.git
cd quickjspp
git fetch origin 0c00c48895919fc02da3f191a2da06addeb07f09
git checkout 0c00c48895919fc02da3f191a2da06addeb07f09
git submodule update --init --depth=1
patch quickjs/quickjs-libc.c -i ../scripts/patches/0001-quickjs-libc-add-realpath-for-Windows.patch
cmake -G "Unix Makefiles" \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_C_FLAGS="-D__MINGW_FENV_DEFINED" .
make quickjs -j4

install -d "$MINGW_PREFIX/lib/quickjs/"
install -m644 quickjs/libquickjs.a "$MINGW_PREFIX/lib/quickjs/"
install -d "$MINGW_PREFIX/include/quickjs"
install -m644 quickjs/quickjs.h quickjs/quickjs-libc.h "$MINGW_PREFIX/include/quickjs/"
install -m644 quickjspp.hpp "$MINGW_PREFIX/include/"
cd ..

git clone --no-checkout https://github.com/PerMalmberg/libcron
cd libcron
git fetch --depth=1 origin ee34810b11bd23c8be637345532f91059b68b2d7
git checkout ee34810b11bd23c8be637345532f91059b68b2d7
git submodule update --init --depth=1
cmake -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$MINGW_PREFIX" .
make libcron install -j4
cd ..

git clone --no-checkout https://github.com/Tencent/rapidjson
cd rapidjson
git fetch --depth=1 origin f54b0e47a08782a6131cc3d60f94d038fa6e0a51
git checkout f54b0e47a08782a6131cc3d60f94d038fa6e0a51
cmake -DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DRAPIDJSON_BUILD_DOC=OFF -DRAPIDJSON_BUILD_EXAMPLES=OFF -DRAPIDJSON_BUILD_TESTS=OFF -DCMAKE_INSTALL_PREFIX="$MINGW_PREFIX" -G "Unix Makefiles" .
make install -j4
cd ..

git clone --no-checkout https://github.com/ToruNiina/toml11
cd toml11
git fetch --depth=1 origin 499be3c177bcf9b42848d5d9567153e4edfcbc8a
git checkout 499be3c177bcf9b42848d5d9567153e4edfcbc8a
cmake -DCMAKE_INSTALL_PREFIX="$MINGW_PREFIX" -G "Unix Makefiles" -DCMAKE_CXX_STANDARD=11 .
make install -j4
cd ..

rm -f C:/Strawberry/perl/bin/pkg-config C:/Strawberry/perl/bin/pkg-config.bat
cmake -DCMAKE_BUILD_TYPE=Release \
      -DSUBCONVERTER_PROJECT_COMMIT="${SUBCONVERTER_PROJECT_COMMIT:-}" \
      -DSUBCONVERTER_BUILD_VERSION="${SUBCONVERTER_BUILD_VERSION:-}" \
      -G "Unix Makefiles" .
make -j4
rm subconverter.exe
# shellcheck disable=SC2046
g++ $(find CMakeFiles/subconverter.dir/src -name "*.obj") curl/lib/libcurl.a -o base/subconverter.exe -static -Wl,--allow-multiple-definition -lbcrypt -lpcre2-8  -llibcron -lyaml-cpp -liphlpapi -lcrypt32 -lws2_32 -lwsock32 -lz  -Lquickjspp/quickjs -lquickjs -s
mv base subconverter
