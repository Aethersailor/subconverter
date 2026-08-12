#!/usr/bin/env python3

"""Remove obsolete container tags and versions after publishing ``latest``."""

import argparse
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


DEFAULT_ATTEMPTS = 5
DEFAULT_TIMEOUT = 30
INDEX_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)


class RegistryCleanupError(RuntimeError):
    """Raised when registry cleanup cannot be completed safely."""


class HttpStatusError(RegistryCleanupError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _request(
    url,
    method="GET",
    headers=None,
    payload=None,
    expected=(200,),
    attempts=DEFAULT_ATTEMPTS,
    timeout=DEFAULT_TIMEOUT,
):
    request_headers = dict(headers or {})
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    for attempt in range(1, attempts + 1):
        request = Request(
            url,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read()
                if response.status not in expected:
                    raise HttpStatusError(
                        response.status,
                        "{} {} returned HTTP {}".format(method, url, response.status),
                    )
                return response.status, response.headers, body
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace").strip()
            retry_after = error.headers.get("Retry-After")
            transient = error.code in (429, 500, 502, 503, 504) or (
                error.code == 403 and retry_after is not None
            )
            if transient and attempt < attempts:
                delay = float(retry_after or min(2 ** (attempt - 1), 10))
                print(
                    "{} {} returned HTTP {}; retrying in {:g}s".format(
                        method, url, error.code, delay
                    ),
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            detail = body[:500] if body else error.reason
            raise HttpStatusError(
                error.code,
                "{} {} returned HTTP {}: {}".format(
                    method, url, error.code, detail
                ),
            )
        except URLError as error:
            if attempt < attempts:
                delay = min(2 ** (attempt - 1), 10)
                print(
                    "{} {} failed: {}; retrying in {}s".format(
                        method, url, error.reason, delay
                    ),
                    file=sys.stderr,
                )
                time.sleep(delay)
                continue
            raise RegistryCleanupError(
                "{} {} failed: {}".format(method, url, error.reason)
            )

    raise AssertionError("request retry loop exited unexpectedly")


def _request_json(*args, **kwargs):
    status, headers, body = _request(*args, **kwargs)
    if not body:
        return status, headers, None
    try:
        return status, headers, json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RegistryCleanupError("response is not valid JSON: {}".format(error))


def dockerhub_tags_to_delete(tags, keep_tag="latest"):
    names = []
    for item in tags:
        name = item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name:
            raise RegistryCleanupError("Docker Hub returned a tag without a name")
        names.append(name)

    if keep_tag not in names:
        raise RegistryCleanupError(
            "refusing cleanup because Docker Hub tag {!r} is missing".format(keep_tag)
        )
    return sorted(name for name in names if name != keep_tag)


def _dockerhub_access_token(username, secret):
    _, _, response = _request_json(
        "https://hub.docker.com/v2/auth/token",
        method="POST",
        payload={"identifier": username, "secret": secret},
    )
    token = response.get("access_token") if isinstance(response, dict) else None
    if not isinstance(token, str) or not token:
        raise RegistryCleanupError("Docker Hub authentication returned no access token")
    return token


def _list_dockerhub_tags(repository, token):
    namespace, name = repository.split("/", 1)
    url = (
        "https://hub.docker.com/v2/namespaces/{}/repositories/{}/tags?{}".format(
            quote(namespace, safe=""),
            quote(name, safe=""),
            urlencode({"page_size": 100}),
        )
    )
    headers = {"Authorization": "Bearer {}".format(token)}
    tags = []
    while url:
        _, _, response = _request_json(url, headers=headers)
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise RegistryCleanupError("Docker Hub returned an invalid tag listing")
        tags.extend(response["results"])
        url = response.get("next")
    return tags


def _delete_dockerhub_tag(repository, tag, token):
    namespace, name = repository.split("/", 1)
    encoded = tuple(quote(value, safe="") for value in (namespace, name, tag))
    urls = (
        "https://hub.docker.com/v2/repositories/{}/{}/tags/{}/".format(*encoded),
        "https://hub.docker.com/v2/namespaces/{}/repositories/{}/tags/{}".format(
            *encoded
        ),
    )
    headers = {"Authorization": "Bearer {}".format(token)}
    for url in urls:
        try:
            _request(
                url,
                method="DELETE",
                headers=headers,
                expected=(200, 202, 204),
            )
            return
        except HttpStatusError as error:
            if error.status not in (404, 405):
                raise


def cleanup_dockerhub(repository, username, secret, keep_tag="latest"):
    if repository.count("/") != 1:
        raise RegistryCleanupError(
            "Docker Hub repository must use the namespace/name form"
        )
    token = _dockerhub_access_token(username, secret)
    tags = _list_dockerhub_tags(repository, token)
    obsolete = dockerhub_tags_to_delete(tags, keep_tag=keep_tag)
    for tag in obsolete:
        _delete_dockerhub_tag(repository, tag, token)

    remaining = [item["name"] for item in _list_dockerhub_tags(repository, token)]
    if remaining != [keep_tag]:
        raise RegistryCleanupError(
            "Docker Hub cleanup incomplete; remaining tags: {}".format(
                ", ".join(sorted(remaining)) or "none"
            )
        )
    print(
        "Docker Hub cleanup complete: removed {} tags; kept {}".format(
            len(obsolete), keep_tag
        )
    )


def manifest_keep_digests(top_digest, manifest):
    if not isinstance(top_digest, str) or not top_digest.startswith("sha256:"):
        raise RegistryCleanupError("registry returned an invalid manifest digest")
    keep = {top_digest}
    children = manifest.get("manifests") if isinstance(manifest, dict) else None
    if not isinstance(children, list) or not children:
        raise RegistryCleanupError(
            "latest is not a non-empty multi-platform image index"
        )
    for child in children:
        digest = child.get("digest") if isinstance(child, dict) else None
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            raise RegistryCleanupError("latest contains an invalid child digest")
        keep.add(digest)
    return keep


def select_ghcr_versions_for_deletion(versions, keep_digests, keep_tag="latest"):
    tagged_latest = []
    obsolete = []
    for version in versions:
        if not isinstance(version, dict) or not isinstance(version.get("id"), int):
            raise RegistryCleanupError("GitHub returned an invalid package version")
        metadata = version.get("metadata") or {}
        container = metadata.get("container") or {}
        tags = container.get("tags") or []
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise RegistryCleanupError("GitHub returned invalid container tags")
        if keep_tag in tags:
            tagged_latest.append(version)
            extra_tags = sorted(set(tags) - {keep_tag})
            if extra_tags:
                raise RegistryCleanupError(
                    "current GHCR latest still has obsolete aliases: {}".format(
                        ", ".join(extra_tags)
                    )
                )
            continue
        if version.get("name") not in keep_digests:
            obsolete.append(version)

    if len(tagged_latest) != 1:
        raise RegistryCleanupError(
            "expected exactly one GHCR version tagged {!r}, found {}".format(
                keep_tag, len(tagged_latest)
            )
        )
    if tagged_latest[0].get("name") not in keep_digests:
        raise RegistryCleanupError("GHCR latest does not match the registry index digest")
    return sorted(obsolete, key=lambda item: (not bool(_version_tags(item)), item["id"]))


def _version_tags(version):
    return ((version.get("metadata") or {}).get("container") or {}).get("tags") or []


def _ghcr_manifest_digests(repository, keep_tag):
    _, _, token_response = _request_json(
        "https://ghcr.io/token?{}".format(
            urlencode(
                {
                    "service": "ghcr.io",
                    "scope": "repository:{}:pull".format(repository),
                }
            )
        )
    )
    token = token_response.get("token") if isinstance(token_response, dict) else None
    if not isinstance(token, str) or not token:
        raise RegistryCleanupError("GHCR registry authentication returned no token")
    _, headers, body = _request(
        "https://ghcr.io/v2/{}/manifests/{}".format(
            repository, quote(keep_tag, safe="")
        ),
        headers={
            "Authorization": "Bearer {}".format(token),
            "Accept": INDEX_ACCEPT,
        },
    )
    digest = headers.get("Docker-Content-Digest")
    try:
        manifest = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RegistryCleanupError("GHCR latest manifest is invalid: {}".format(error))
    return manifest_keep_digests(digest, manifest)


def _github_headers(token):
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer {}".format(token),
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "subconverter-registry-cleanup",
    }


def _list_ghcr_versions(owner, package, token):
    base = "https://api.github.com/users/{}/packages/container/{}/versions".format(
        quote(owner, safe=""), quote(package, safe="")
    )
    headers = _github_headers(token)
    versions = []
    page = 1
    while True:
        _, _, response = _request_json(
            "{}?{}".format(base, urlencode({"per_page": 100, "page": page})),
            headers=headers,
        )
        if not isinstance(response, list):
            raise RegistryCleanupError("GitHub returned an invalid package version list")
        versions.extend(response)
        if len(response) < 100:
            return versions
        page += 1


def _delete_ghcr_version(owner, package, version_id, token):
    url = (
        "https://api.github.com/users/{}/packages/container/{}/versions/{}".format(
            quote(owner, safe=""), quote(package, safe=""), version_id
        )
    )
    _request(
        url,
        method="DELETE",
        headers=_github_headers(token),
        expected=(204,),
    )


def _wait_for_current_ghcr_versions(
    owner, package, token, keep_digests, keep_tag, attempts=12
):
    last_error = None
    for attempt in range(1, attempts + 1):
        versions = _list_ghcr_versions(owner, package, token)
        try:
            select_ghcr_versions_for_deletion(
                versions, keep_digests, keep_tag=keep_tag
            )
            return versions
        except RegistryCleanupError as error:
            last_error = error
            if attempt < attempts:
                time.sleep(5)
    raise RegistryCleanupError(
        "GHCR package metadata did not converge: {}".format(last_error)
    )


def cleanup_ghcr(repository, owner, token, keep_tag="latest"):
    if repository.count("/") != 1:
        raise RegistryCleanupError("GHCR repository must use the owner/name form")
    package = repository.split("/", 1)[1]
    keep_digests = _ghcr_manifest_digests(repository, keep_tag)
    versions = _wait_for_current_ghcr_versions(
        owner, package, token, keep_digests, keep_tag
    )
    obsolete = select_ghcr_versions_for_deletion(
        versions, keep_digests, keep_tag=keep_tag
    )
    for version in obsolete:
        _delete_ghcr_version(owner, package, version["id"], token)

    leftovers = []
    for attempt in range(1, 7):
        remaining = _list_ghcr_versions(owner, package, token)
        leftovers = select_ghcr_versions_for_deletion(
            remaining, keep_digests, keep_tag=keep_tag
        )
        if not leftovers:
            break
        if attempt < 6:
            time.sleep(5)
    if leftovers:
        raise RegistryCleanupError(
            "GHCR cleanup incomplete; {} obsolete versions remain".format(
                len(leftovers)
            )
        )
    print(
        "GHCR cleanup complete: removed {} versions; kept latest and {} child manifests".format(
            len(obsolete), len(keep_digests) - 1
        )
    )


def parse_arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="registry", required=True)

    dockerhub = subparsers.add_parser("dockerhub")
    dockerhub.add_argument("--repository", required=True)
    dockerhub.add_argument("--keep-tag", default="latest")

    ghcr = subparsers.add_parser("ghcr")
    ghcr.add_argument("--repository", required=True)
    ghcr.add_argument("--owner", required=True)
    ghcr.add_argument("--keep-tag", default="latest")
    return parser.parse_args(argv)


def _required_environment(name):
    value = os.environ.get(name)
    if not value:
        raise RegistryCleanupError("required environment variable {} is missing".format(name))
    return value


def main(argv=None):
    options = parse_arguments(argv)
    if options.registry == "dockerhub":
        cleanup_dockerhub(
            options.repository,
            _required_environment("DOCKER_USERNAME"),
            _required_environment("DOCKER_PASSWORD"),
            keep_tag=options.keep_tag,
        )
    elif options.registry == "ghcr":
        cleanup_ghcr(
            options.repository,
            options.owner,
            _required_environment("GH_TOKEN"),
            keep_tag=options.keep_tag,
        )
    else:
        raise AssertionError("unsupported registry")


if __name__ == "__main__":
    try:
        main()
    except RegistryCleanupError as error:
        print("registry cleanup failed: {}".format(error), file=sys.stderr)
        raise SystemExit(1)
