"""Redaction for diagnostics; never persist requests, cookies or JWTs."""
import re


def redact(value, secrets=()):
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[hidden]')
    text = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9._~+/-]+', r'\1[hidden]', text)
    text = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', '[JWT hidden]', text)
    text = re.sub(r'''(?ix)((?:["']?)(?:cookie|cookies|authorization|token|access_token|refresh_token|xsec_token|password|secret|tunnel_token|cf-access-jwt-assertion)(?:["']?)\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;&}]+)''', r'\1[hidden]', text)
    # Cookie response/request headers can contain several semicolon-separated keys.
    text = re.sub(r'(?im)(^\s*(?:set-cookie|cookie)\s*:\s*).*$', r'\1[hidden]', text)
    text = re.sub(r'wss?://(?:localhost|127\.0\.0\.1|\[::1\]):\d+/devtools/[^\s\"\']+', '[local browser endpoint hidden]', text)
    return text[:8000]
