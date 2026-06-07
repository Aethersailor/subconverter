#!/bin/bash
set -xe

brew reinstall rapidjson zlib pcre2 pkgconfig

#git clone https://github.com/curl/curl --depth=1 --branch curl-7_88_1
#cd curl
#./buildconf > /dev/null
#./configure --with-ssl=/usr/local/opt/openssl@1.1 --without-mbedtls --disable-ldap --disable-ldaps --disable-rtsp --without-libidn2 > /dev/null
#cmake -DCMAKE_USE_SECTRANSP=ON -DHTTP_ONLY=ON -DBUILD_TESTING=OFF -DBUILD_SHARED_LIBS=OFF -DCMAKE_USE_LIBSSH2=OFF . > /dev/null
#make -j8 > /dev/null
#cd ..

git clone --no-checkout https://github.com/jbeder/yaml-cpp
cd yaml-cpp
git fetch --depth=1 origin f7320141120f720aecc4c32be25586e7da9eb978
git checkout f7320141120f720aecc4c32be25586e7da9eb978
cmake -DCMAKE_BUILD_TYPE=Release -DYAML_CPP_BUILD_TESTS=OFF -DYAML_CPP_BUILD_TOOLS=OFF . > /dev/null
make -j6 > /dev/null
sudo make install > /dev/null
cd ..

git clone --no-checkout https://github.com/ftk/quickjspp.git
cd quickjspp
git fetch origin 0c00c48895919fc02da3f191a2da06addeb07f09
git checkout 0c00c48895919fc02da3f191a2da06addeb07f09
git submodule update --init --depth=1
cmake -DCMAKE_BUILD_TYPE=Release .
make quickjs -j6 > /dev/null
sudo install -d /usr/local/lib/quickjs/
sudo install -m644 quickjs/libquickjs.a /usr/local/lib/quickjs/
sudo install -d /usr/local/include/quickjs/
sudo install -m644 quickjs/quickjs.h quickjs/quickjs-libc.h /usr/local/include/quickjs/
sudo install -m644 quickjspp.hpp /usr/local/include/
cd ..

git clone --no-checkout https://github.com/PerMalmberg/libcron
cd libcron
git fetch --depth=1 origin ee34810b11bd23c8be637345532f91059b68b2d7
git checkout ee34810b11bd23c8be637345532f91059b68b2d7
git submodule update --init --depth=1
cmake -DCMAKE_BUILD_TYPE=Release .
make libcron -j6
sudo install -m644 libcron/out/Release/liblibcron.a /usr/local/lib/
sudo install -d /usr/local/include/libcron/
sudo install -m644 libcron/include/libcron/* /usr/local/include/libcron/
sudo install -d /usr/local/include/date/
sudo install -m644 libcron/externals/date/include/date/* /usr/local/include/date/
cd ..

git clone --no-checkout https://github.com/ToruNiina/toml11
cd toml11
git fetch --depth=1 origin 499be3c177bcf9b42848d5d9567153e4edfcbc8a
git checkout 499be3c177bcf9b42848d5d9567153e4edfcbc8a
cmake -DCMAKE_CXX_STANDARD=11 .
sudo make install -j6 > /dev/null
cd ..

cmake -DCMAKE_BUILD_TYPE=Release \
      -DSUBCONVERTER_PROJECT_COMMIT="${SUBCONVERTER_PROJECT_COMMIT:-}" \
      -DSUBCONVERTER_BUILD_VERSION="${SUBCONVERTER_BUILD_VERSION:-}" .
make -j6
rm subconverter
# shellcheck disable=SC2046
c++ -Xlinker -unexported_symbol -Xlinker "*" -o base/subconverter -framework CoreFoundation -framework Security $(find CMakeFiles/subconverter.dir/src/ -name "*.o") "$(brew --prefix zlib)/lib/libz.a" "$(brew --prefix pcre2)/lib/libpcre2-8.a" $(find . -name "*.a") -lcurl -O3

cd base
chmod +rx subconverter
chmod +r ./*
cd ..
mv base subconverter

set +xe
