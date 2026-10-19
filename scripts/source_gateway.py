"""Fail-closed destination policy. No redirect target is ever requested.

The transport retains urllib's normal proxy and TLS behavior. This guard is not
permission to lift a hold, and does not replace the platform's network review.
Only URL metadata is written to the ignored audit log; never response bodies.
"""
import datetime as dt
import ipaddress
import json
import pathlib
import re
import threading
import urllib.parse
import urllib.request

from source_control import controls, held_host

_AUDIT_LOCK = threading.Lock()
CVE_REVISION = 'https://api.github.com/repos/CVEProject/cvelistV5/git/ref/heads/main'


class SourcePolicyError(RuntimeError):
    pass


class RedirectBlocked(SourcePolicyError):
    pass


def metadata_url(url):
    """Retain provenance without persisting credentials or signed query values."""
    try:
        value = urllib.parse.urlsplit(str(url))
        host = value.hostname or ''
        if ':' in host:
            host = '[' + host + ']'
        if value.port:
            host += ':' + str(value.port)
        query = urllib.parse.urlencode([(key, '[redacted]') for key, _ in urllib.parse.parse_qsl(value.query, keep_blank_values=True)])
        return urllib.parse.urlunsplit((value.scheme, host, value.path, query, ''))[:2048]
    except ValueError:
        return '[invalid URL]'


def audit(root, adapter, identity, url, outcome, **metadata):
    if 'redirectLocation' in metadata:
        metadata['redirectLocation'] = metadata_url(metadata['redirectLocation'])
    row = {'at': dt.datetime.now(dt.timezone.utc).isoformat(), 'adapter': adapter,
           'source': identity, 'url': metadata_url(url), 'outcome': outcome, **metadata}
    with _AUDIT_LOCK:
        directory = pathlib.Path(root) / '.sites-runtime'
        directory.mkdir(exist_ok=True)
        with (directory / 'source-request-audit.jsonl').open('a') as output:
            output.write(json.dumps(row, ensure_ascii=False) + '\n')


def public_https(url):
    if not isinstance(url, str) or re.search(r'[\s\\\x00-\x1f\x7f]', url):
        raise SourcePolicyError('invalid_url')
    try:
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise SourcePolicyError('invalid_url') from None
    if parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.fragment or port not in (None, 443):
        raise SourcePolicyError('https_public_host_required')
    if host.endswith('.') or '.' not in host or host in ('localhost', 'localhost.localdomain') or host.endswith(('.local', '.localhost', '.internal', '.lan', '.home', '.test', '.invalid')):
        raise SourcePolicyError('private_or_reserved_host')
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise SourcePolicyError('ip_literal_not_registered')
    if not re.fullmatch(r'[A-Za-z]{2,63}', host.rsplit('.', 1)[-1]):
        raise SourcePolicyError('numeric_or_nonpublic_host_syntax')
    return parsed


def target_rule(root, adapter, identity, url):
    """Pure local validation, used by the offline plan; grants no network access."""
    parsed = public_https(url)
    registry = json.loads((pathlib.Path(root) / 'config/source-registry.json').read_text())
    if adapter == 'rss':
        source = next((s for s in registry if s.get('enabled', True) and s['name'] == identity and s['url'] == url), None)
        if source:
            return source['url']
    elif adapter == 'ithome_metadata':
        source = next((s for s in registry if s.get('enabled', True) and s['name'] == 'iThome'), None)
        if source and identity == 'iThome' and parsed.netloc == 'www.ithome.com.tw' and re.fullmatch(r'/news/[0-9]+', parsed.path) and not parsed.query:
            return source['url']
    elif adapter == 'steam':
        watch = json.loads((pathlib.Path(root) / 'config/steam-deals.json').read_text())
        for item in watch:
            app = str(item['appId'])
            region = item.get('storeRegion', 'HK').upper()
            expected = 'https://store.steampowered.com/api/appdetails?' + urllib.parse.urlencode({'appids': app, 'cc': region.lower(), 'l': 'english'})
            if identity == app and url == expected:
                return url
    elif adapter == 'cve' and identity == 'CVE List V5':
        if url == CVE_REVISION:
            return url
        prefix = r'https://raw\.githubusercontent\.com/CVEProject/cvelistV5/[0-9a-f]{40}/cves/'
        if re.fullmatch(prefix + r'deltaLog\.json', url):
            return url
        match = re.fullmatch(prefix + r'([0-9]{4})/([0-9]+)xxx/(CVE-([0-9]{4})-([0-9]{4,19}))\.json', url)
        if match and match[1] == match[4] and match[2] == match[5][:-3]:
            return url
    raise SourcePolicyError('unregistered_destination')


def authorize_request(root, adapter, identity, url):
    """Every production request must pass both the global and source holds."""
    try:
        policy = controls(pathlib.Path(root))
        if policy.get('networkRefreshHold'):
            raise SourcePolicyError('network_refresh_held')
        source_url = target_rule(root, adapter, identity, url)
        if held_host(url, policy):
            raise SourcePolicyError('hostname_held')
        if adapter in ('rss', 'ithome_metadata') and source_url in policy.get('rss', {}):
            raise SourcePolicyError('source_held')
        if adapter == 'cve' and policy.get('cve'):
            raise SourcePolicyError('cve_held')
    except SourcePolicyError as error:
        audit(root, adapter, identity, url, 'blocked', error=str(error))
        raise
    audit(root, adapter, identity, url, 'authorized_initial_url')


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, root, adapter, identity):
        self.root, self.adapter, self.identity = root, adapter, identity

    def redirect_request(self, request, response, code, message, headers, newurl):
        # Record metadata, but never open or resolve the redirect destination.
        audit(self.root, self.adapter, self.identity, request.full_url, 'redirect_blocked',
              httpStatus=code, redirectLocation=headers.get('Location', '')[:2048])
        raise RedirectBlocked('redirect_target_not_requested')


def open_source(root, adapter, identity, url, headers=None, timeout=25):
    authorize_request(root, adapter, identity, url)
    # Do not silently discard an opener installed by platform/bootstrap code.
    # This application does not install one; a future runtime change needs review.
    if urllib.request._opener is not None:
        audit(root, adapter, identity, url, 'blocked', error='installed_opener_requires_review')
        raise SourcePolicyError('installed_opener_requires_review')
    request = urllib.request.Request(url, headers=headers or {})
    # Do not replace ProxyHandler, TLS context, certificate verification or DNS.
    # The platform's existing egress route and access reviewer remain in force.
    opener = urllib.request.build_opener(RejectRedirects(root, adapter, identity))
    try:
        response = opener.open(request, timeout=timeout)
        if response.geturl() != url:
            response.close()
            raise SourcePolicyError('unexpected_response_url')
        audit(root, adapter, identity, url, 'response', httpStatus=response.status)
        return response
    except Exception as error:
        audit(root, adapter, identity, url, 'request_failed', errorType=type(error).__name__)
        raise
