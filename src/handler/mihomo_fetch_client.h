#ifndef MIHOMO_FETCH_CLIENT_H_INCLUDED
#define MIHOMO_FETCH_CLIENT_H_INCLUDED

#include "handler/webget.h"

// Executes one strict subscription-provider request through the bundled,
// identity-checked Mihomo helper. It never falls back to libcurl.
int mihomoFetch(const FetchArgument &argument, FetchResult &result);

#endif // MIHOMO_FETCH_CLIENT_H_INCLUDED
