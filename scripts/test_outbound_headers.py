#!/usr/bin/env python3

import argparse
import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class CaptureHandler(BaseHTTPRequestHandler):
    captured_headers = None
    captured_event = threading.Event()

    def do_GET(self):
        type(self).captured_headers = {key.lower(): value for key, value in self.headers.items()}
        type(self).captured_event.set()
        proxy = "ss://YWVzLTEyOC1nY206cGFzc3dvcmQ=@127.0.0.1:8388#header-test"
        body = base64.b64encode(proxy.encode("utf-8"))
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


def wait_for_service(service_url):
    last_error = None
    for _ in range(60):
        try:
            with urllib.request.urlopen(service_url + "/version", timeout=2) as response:
                return response.read().decode("utf-8").strip()
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError("service did not become ready: {}".format(last_error))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-url", required=True)
    parser.add_argument("--echo-host", default="127.0.0.1")
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args()

    version_response = wait_for_service(args.service_url)
    expected_response = "subconverter {} backend".format(args.expected_version)
    if version_response != expected_response:
        raise RuntimeError(
            "unexpected /version response: {!r}, expected {!r}".format(
                version_response, expected_response
            )
        )

    server = ThreadingHTTPServer(("0.0.0.0", 0), CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        subscription_url = "http://{}:{}/subscription".format(
            args.echo_host, server.server_address[1]
        )
        query = urllib.parse.urlencode(
            {"target": "mixed", "url": subscription_url}
        )
        request_url = args.service_url + "/sub?" + query
        with urllib.request.urlopen(request_url, timeout=30) as response:
            response.read()

        if not CaptureHandler.captured_event.wait(10):
            raise RuntimeError("subconverter did not request the echo subscription")

        headers = CaptureHandler.captured_headers
        forbidden = {
            "subconverter-request",
            "subconverter-version",
            "x-requested-with",
            "content-type",
        }
        present = sorted(forbidden.intersection(headers))
        if present:
            raise RuntimeError("fingerprint headers were sent: " + ", ".join(present))

        with open(".github/project-metadata.json", encoding="utf-8") as stream:
            expected_user_agent = json.load(stream)["user_agent"]
        if headers.get("user-agent") != expected_user_agent:
            raise RuntimeError(
                "unexpected User-Agent: {!r}, expected {!r}".format(
                    headers.get("user-agent"), expected_user_agent
                )
            )

        expected_host = "{}:{}".format(args.echo_host, server.server_address[1])
        if headers.get("host") != expected_host:
            raise RuntimeError(
                "entry Host header leaked upstream: {!r}, expected {!r}".format(
                    headers.get("host"), expected_host
                )
            )
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
