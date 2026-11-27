#!/usr/bin/env node

// URL policy shared by third-party preprocessing and local-history display.
// Keep this module dependency-free so it can run with the required Node.js 22+.

const SENSITIVE_NAME = /(?:^|[_-])(?:token|key|secret|auth|authorization|signature|sig|session(?:id)?|cookie|credential|password|passwd|pwd|jwt|assertion|code|email|mail|phone|mobile)(?:$|[_-])/i;
const SENSITIVE_PATH = /(?:^|[\/_-])(token|key|secret|auth|signature|session|credential|password|login|log-in|signin|sign-in|oauth|sso|authorize|authorization|callback|share|invite|invitation|magic-link)(?:[\/_=:-]|$)/i;
const COMPACT_SENSITIVE_NAMES = new Set([
  'apikey', 'accesskey', 'secretkey', 'privatekey', 'clientsecret', 'authkey',
  'accesstoken', 'refreshtoken', 'idtoken', 'authtoken', 'sessionid', 'sessionkey',
  'authorizationcode', 'verificationcode', 'authcode', 'emailaddress', 'phonenumber',
  'credentials', 'csrf', 'csrftoken', 'xsrf', 'xsrftoken', 'sid',
]);

function sensitiveName(value) {
  const name = String(value).split(/[?/#]/).at(-1).replace(/([a-z0-9])([A-Z])/g, '$1_$2').replace(/[.\[\]]/g, '_');
  return SENSITIVE_NAME.test(name) || COMPACT_SENSITIVE_NAMES.has(name.toLowerCase().replace(/[_-]/g, ''));
}

function sensitiveValue(value) {
  return /[^\s/@]+@[^\s/@]+\.[^\s/@]+/.test(value) ||
    /^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(value);
}

function ipv4IsPrivate(host) {
  const octets = host.split('.').map(Number);
  if (octets.length !== 4 || octets.some(n => !Number.isInteger(n) || n < 0 || n > 255)) return false;
  const [a, b] = octets;
  const value = (((a * 256) + b) * 256 + octets[2]) * 256 + octets[3];
  const inRange = (start, end) => value >= start && value <= end;
  return inRange(0x00000000, 0x00ffffff) || // "this" network / unspecified
    inRange(0x0a000000, 0x0affffff) || // private
    inRange(0x64400000, 0x647fffff) || // shared address space (100.64/10)
    inRange(0x7f000000, 0x7fffffff) || // loopback
    inRange(0xa9fe0000, 0xa9feffff) || // link-local
    inRange(0xac100000, 0xac1fffff) || // private
    inRange(0xc0000000, 0xc00000ff) || // IETF protocol assignments
    inRange(0xc0000200, 0xc00002ff) || // TEST-NET-1
    inRange(0xc0a80000, 0xc0a8ffff) || // private
    inRange(0xc6120000, 0xc613ffff) || // benchmarking
    inRange(0xc6336400, 0xc63364ff) || // TEST-NET-2
    inRange(0xcb007100, 0xcb0071ff) || // TEST-NET-3
    inRange(0xe0000000, 0xffffffff); // multicast / reserved
}

function parseIpv6(host) {
  let value = host.toLowerCase();
  if (!value.includes(':')) return null;

  // URL normalizes IPv4-mapped addresses to hexadecimal, but accepting the
  // dotted form here keeps this helper safe when called directly in tests or
  // by another local module.
  if (value.includes('.')) {
    const separator = value.lastIndexOf(':');
    if (separator < 0) return null;
    const octets = value.slice(separator + 1).split('.').map(Number);
    if (octets.length !== 4 || octets.some(n => !Number.isInteger(n) || n < 0 || n > 255)) return null;
    const high = ((octets[0] << 8) | octets[1]).toString(16);
    const low = ((octets[2] << 8) | octets[3]).toString(16);
    value = `${value.slice(0, separator)}:${high}:${low}`;
  }

  const pieces = value.split('::');
  if (pieces.length > 2) return null;
  const left = pieces[0] ? pieces[0].split(':') : [];
  const right = pieces.length === 2 && pieces[1] ? pieces[1].split(':') : [];
  if (pieces.length === 1 && left.length !== 8) return null;
  if (pieces.length === 2 && left.length + right.length >= 8) return null;

  const groups = pieces.length === 2
    ? [...left, ...Array(8 - left.length - right.length).fill('0'), ...right]
    : left;
  const parsed = groups.map(group => /^[0-9a-f]{1,4}$/.test(group) ? parseInt(group, 16) : NaN);
  return parsed.length === 8 && parsed.every(Number.isInteger) ? parsed : null;
}

function ipv6IsPrivate(groups) {
  if (!groups) return false;
  const first = groups[0];
  const isUnspecified = groups.every(group => group === 0);
  const isLoopback = groups.slice(0, 7).every(group => group === 0) && groups[7] === 1;
  if (isUnspecified || isLoopback) return true;

  // fc00::/7 (ULA) and fe80::/10 (link-local).
  if ((first & 0xfe00) === 0xfc00 || (first & 0xffc0) === 0xfe80) return true;

  // ::ffff:0:0/96 IPv4-mapped IPv6 addresses inherit the IPv4 policy.
  const mapped = groups.slice(0, 5).every(group => group === 0) && groups[5] === 0xffff;
  if (mapped) {
    const octets = [groups[6] >> 8, groups[6] & 0xff, groups[7] >> 8, groups[7] & 0xff];
    return ipv4IsPrivate(octets.join('.'));
  }
  return false;
}

function hostnameIsPrivate(hostname) {
  const host = hostname.replace(/^\[|\]$/g, '').toLowerCase().replace(/\.$/, '');
  if (!host || host === 'localhost' || /\.(?:localhost|local|internal|lan|home)$/.test(host) || (!host.includes('.') && !host.includes(':'))) return true;
  if (ipv4IsPrivate(host)) return true;
  return ipv6IsPrivate(parseIpv6(host));
}

function parseHttpUrl(raw) {
  let parsed;
  try { parsed = new URL(String(raw)); } catch { return null; }
  if (!['http:', 'https:'].includes(parsed.protocol)) return null;
  return parsed;
}

function decodeFragment(raw) {
  try { return decodeURIComponent(raw); } catch { return null; }
}

function fragmentSegments(raw) {
  return raw.split(/[&;]/);
}

function fragmentHasSensitiveName(raw) {
  const decoded = decodeFragment(raw);
  if (decoded === null) return sensitiveName(raw);
  return fragmentSegments(decoded).some(segment => {
    const separator = segment.search(/[=:]/);
    const name = separator === -1 ? segment : segment.slice(0, separator);
    return sensitiveName(name) || sensitiveValue(separator === -1 ? segment : segment.slice(separator + 1));
  });
}

function redactFragment(raw) {
  if (decodeFragment(raw) === null) return sensitiveName(raw) ? '[REDACTED]' : raw;
  const parts = raw.split(/([&;])/);
  for (let i = 0; i < parts.length; i += 2) {
    const segment = parts[i];
    const separator = segment.search(/[=:]/);
    if (separator !== -1) {
      const name = decodeFragment(segment.slice(0, separator)) || segment.slice(0, separator);
      const value = decodeFragment(segment.slice(separator + 1)) || segment.slice(separator + 1);
      if (sensitiveName(name) || sensitiveValue(value)) parts[i] = `${segment.slice(0, separator)}${segment[separator]}%5BREDACTED%5D`;
    } else if (fragmentHasSensitiveName(segment)) {
      const decoded = decodeFragment(segment) || segment;
      // If the delimiters themselves were percent-encoded (for example
      // access_token%3Dabc), redact the whole component so the value cannot
      // survive in the output.
      parts[i] = /[=:]/.test(decoded) || sensitiveValue(decoded) ? '%5BREDACTED%5D' : `${segment}=%5BREDACTED%5D`;
    }
  }
  return parts.join('');
}

export function classifyUrlForJina(raw) {
  const parsed = parseHttpUrl(raw);
  if (!parsed) return { ok: false, reason: 'invalid_or_unsupported_scheme' };
  if (parsed.username || parsed.password) return { ok: false, reason: 'embedded_credentials' };
  if (hostnameIsPrivate(parsed.hostname)) return { ok: false, reason: 'private_or_local_host' };
  for (const [name, value] of parsed.searchParams) {
    if (sensitiveName(name) || sensitiveValue(value)) return { ok: false, reason: 'sensitive_query_parameter' };
  }
  const fragment = parsed.hash.slice(1);
  if (fragmentHasSensitiveName(fragment)) return { ok: false, reason: 'sensitive_fragment' };
  let decodedPath = parsed.pathname;
  try { decodedPath = decodeURIComponent(decodedPath); } catch { return { ok: false, reason: 'malformed_encoding' }; }
  if (SENSITIVE_PATH.test(decodedPath) || sensitiveValue(decodedPath)) return { ok: false, reason: 'sensitive_path' };
  return { ok: true, url: parsed.href };
}

export function redactUrl(raw) {
  const relative = typeof raw === 'string' && /^[/?#]/.test(raw);
  let resolved = raw;
  try { if (relative) resolved = new URL(raw, 'https://relative.invalid/').href; }
  catch { return '[INVALID URL]'; }
  const parsed = parseHttpUrl(resolved);
  if (!parsed) return String(raw || '');
  parsed.username = '';
  parsed.password = '';
  const entries = [...parsed.searchParams];
  parsed.search = '';
  for (const [name, value] of entries) {
    parsed.searchParams.append(name, sensitiveName(name) || sensitiveValue(value) ? '[REDACTED]' : value);
  }
  const fragment = parsed.hash.slice(1);
  if (fragment && fragmentHasSensitiveName(fragment)) parsed.hash = redactFragment(fragment);
  let sensitivePath = false;
  parsed.pathname = parsed.pathname.split('/').map(segment => {
    let decoded;
    try { decoded = decodeURIComponent(segment); } catch { decoded = segment; }
    sensitivePath ||= SENSITIVE_PATH.test(decoded) || sensitiveValue(decoded);
    return sensitivePath && segment ? '%5BREDACTED%5D' : segment;
  }).join('/');
  return relative ? parsed.pathname + parsed.search + parsed.hash : parsed.href;
}

export { hostnameIsPrivate };
