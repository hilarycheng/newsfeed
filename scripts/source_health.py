"""Compact deterministic source health; no fetching or article inspection."""
from collections import Counter


def source_counts(statuses):
    counts = Counter(row.get('status', 'unknown') for row in statuses)
    return {'sourceCount': len(statuses), 'sourceSuccessCount': counts['ok'],
            'sourceFailureCount': counts['failed'], 'sourceSkippedCount': counts['skipped'],
            'sourceLimitedCount': counts['limited']}


def require_rss_health(statuses):
    """One failed source is not fatal. Preserve the existing 70% / 10 gate."""
    counts = source_counts(statuses)
    if counts['sourceSuccessCount'] < max(10, len(statuses) * 0.7):
        raise RuntimeError('Too many source failures; previous edition preserved')
    return counts


def source_failure(source, error):
    """Return registered source identity, never exception text/response contents.

    These are ordinary adapter errors only. External platform review denials
    must stop the attempted operation and cannot be converted into this record.
    """
    import urllib.error
    import xml.etree.ElementTree as ET
    from source_gateway import RedirectBlocked, SourcePolicyError
    if isinstance(error, RedirectBlocked):
        code = 'redirect_blocked'
    elif isinstance(error, SourcePolicyError):
        code = 'destination_policy'
    elif isinstance(error, urllib.error.HTTPError):
        code = 'http_error'
    elif isinstance(error, (TimeoutError, urllib.error.URLError)):
        code = 'transport_error'
    elif isinstance(error, ET.ParseError):
        code = 'invalid_feed_xml'
    elif isinstance(error, ValueError):
        code = 'invalid_feed_payload'
    else:
        code = 'source_processing_error'
    result = {'name': source['name'], 'status': 'failed',
              'sourceId': source.get('id'), 'errorCode': code,
              'errorType': type(error).__name__, 'detail': code}
    if isinstance(error, urllib.error.HTTPError):
        result['httpStatus'] = error.code
    return {'source': result, 'candidates': []}
