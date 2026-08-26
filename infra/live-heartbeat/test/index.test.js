import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import worker, { buildDispatchRequest, classifyResponse, dispatchWithOneRetry } from '../src/index.js';
// Loaded as raw text via Vite's `?raw` suffix rather than node:fs -- these
// tests run inside the Workers runtime (Miniflare), which doesn't expose a
// real filesystem the way plain Node does.
import source from '../src/index.js?raw';

const FAKE_TOKEN = 'github_pat_FAKE_TEST_TOKEN_do_not_reuse_1234567890';

describe('buildDispatchRequest', () => {
  it('targets the correct GitHub endpoint', () => {
    const req = buildDispatchRequest(FAKE_TOKEN);
    expect(req.url).toBe(
      'https://api.github.com/repos/werriesjacob1-cmyk/Full-Count/actions/workflows/dashboard-live.yml/dispatches',
    );
  });

  it('uses POST', () => {
    const req = buildDispatchRequest(FAKE_TOKEN);
    expect(req.method).toBe('POST');
  });

  it('dispatches against ref main', async () => {
    const req = buildDispatchRequest(FAKE_TOKEN);
    const body = await req.clone().json();
    expect(body).toEqual({ ref: 'main' });
  });

  it('sends the correct Accept and API-version headers', () => {
    const req = buildDispatchRequest(FAKE_TOKEN);
    expect(req.headers.get('Accept')).toBe('application/vnd.github+json');
    expect(req.headers.get('X-GitHub-Api-Version')).toBe('2022-11-28');
  });

  it('takes Authorization from the passed token, not a hardcoded value', () => {
    const req = buildDispatchRequest(FAKE_TOKEN);
    expect(req.headers.get('Authorization')).toBe(`Bearer ${FAKE_TOKEN}`);

    const otherToken = 'a-completely-different-token';
    const req2 = buildDispatchRequest(otherToken);
    expect(req2.headers.get('Authorization')).toBe(`Bearer ${otherToken}`);
  });

  it('throws if no token is provided (fails closed, never sends an unauthenticated dispatch)', () => {
    expect(() => buildDispatchRequest(undefined)).toThrow(/missing GITHUB_PAT/);
    expect(() => buildDispatchRequest('')).toThrow(/missing GITHUB_PAT/);
  });
});

describe('classifyResponse', () => {
  it('204 is success', () => {
    const outcome = classifyResponse(new Response(null, { status: 204 }));
    expect(outcome).toEqual({ ok: true, status: 204, retryable: false, reason: 'dispatched' });
  });

  it('401 is a clear, non-retryable auth failure', () => {
    const outcome = classifyResponse(new Response(null, { status: 401 }));
    expect(outcome.ok).toBe(false);
    expect(outcome.retryable).toBe(false);
    expect(outcome.reason).toBe('auth_failure');
  });

  it('403 is a clear, non-retryable auth failure', () => {
    const outcome = classifyResponse(new Response(null, { status: 403 }));
    expect(outcome.ok).toBe(false);
    expect(outcome.retryable).toBe(false);
    expect(outcome.reason).toBe('auth_failure');
  });

  it('404 is a clear, non-retryable configuration failure', () => {
    const outcome = classifyResponse(new Response(null, { status: 404 }));
    expect(outcome.ok).toBe(false);
    expect(outcome.retryable).toBe(false);
    expect(outcome.reason).toBe('not_found_or_no_access');
  });

  it('5xx is retryable', () => {
    for (const status of [500, 502, 503, 504]) {
      const outcome = classifyResponse(new Response(null, { status }));
      expect(outcome.ok).toBe(false);
      expect(outcome.retryable).toBe(true);
    }
  });
});

describe('dispatchWithOneRetry', () => {
  it('succeeds immediately on 204, no retry', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const outcome = await dispatchWithOneRetry(FAKE_TOKEN, fetchImpl);
    expect(outcome.ok).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('does NOT retry on 401 -- a config problem a retry cannot fix', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    const outcome = await dispatchWithOneRetry(FAKE_TOKEN, fetchImpl);
    expect(outcome.ok).toBe(false);
    expect(outcome.reason).toBe('auth_failure');
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('does NOT retry on 404', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 404 }));
    const outcome = await dispatchWithOneRetry(FAKE_TOKEN, fetchImpl);
    expect(outcome.ok).toBe(false);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('retries exactly once on a transient 5xx, then succeeds', async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const outcome = await dispatchWithOneRetry(FAKE_TOKEN, fetchImpl);
    expect(outcome.ok).toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('fails visibly after 5xx persists through the one retry', async () => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(null, { status: 502 }));
    const outcome = await dispatchWithOneRetry(FAKE_TOKEN, fetchImpl);
    expect(outcome.ok).toBe(false);
    expect(outcome.reason).toBe('transient_server_error');
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('a network error retries once, then fails visibly', async () => {
    const fetchImpl = vi.fn().mockRejectedValue(new TypeError('network error'));
    const outcome = await dispatchWithOneRetry(FAKE_TOKEN, fetchImpl);
    expect(outcome.ok).toBe(false);
    expect(outcome.reason).toBe('network_error');
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });
});

describe('scheduled() handler -- secret handling and logging', () => {
  let logSpy;
  let errorSpy;

  beforeEach(() => {
    logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
    errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    logSpy.mockRestore();
    errorSpy.mockRestore();
  });

  function allLoggedText() {
    return [...logSpy.mock.calls, ...errorSpy.mock.calls].flat().map(String).join('\n');
  }

  it('never logs the secret token, on success', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    await worker.scheduled({ cron: '*/5 * * * *' }, { GITHUB_PAT: FAKE_TOKEN }, {});
    expect(allLoggedText()).not.toContain(FAKE_TOKEN);
  });

  it('never logs the secret token, on a thrown failure', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    await expect(
      worker.scheduled({ cron: '*/5 * * * *' }, { GITHUB_PAT: FAKE_TOKEN }, {}),
    ).rejects.toThrow();
    expect(allLoggedText()).not.toContain(FAKE_TOKEN);
  });

  it('fails visibly (logs + throws) when the secret is simply missing', async () => {
    await expect(worker.scheduled({ cron: '*/5 * * * *' }, {}, {})).resolves.toBeUndefined();
    expect(allLoggedText()).toContain('missing_secret');
  });
});

describe('fetch() handler -- no public API, never touches GitHub or the secret', () => {
  it('returns a static message and does not call fetch()', async () => {
    const spy = vi.fn();
    globalThis.fetch = spy;
    const res = await worker.fetch();
    expect(res.status).toBe(200);
    const text = await res.text();
    expect(text).toContain('No public API');
    expect(spy).not.toHaveBeenCalled();
  });
});

describe('structural contract: this Worker is a clock, not business logic', () => {
  // Strip `//` line comments first -- this file's header comment legitimately
  // *discusses* MLB/FanDuel/git in prose while explaining the boundary this
  // test enforces. The actual contract is about executable code, not prose;
  // scanning post-strip avoids false positives on the comment ABOUT the ban.
  const codeOnly = source
    .split('\n')
    .map((line) => line.replace(/\/\/.*$/, ''))
    .join('\n');

  it('code (excluding comments) contains no baseball/model/grading/pricing/git-write logic', () => {
    const forbidden = [
      /statsapi/i,
      /fanduel/i,
      /\bodds\b/i,
      /grade_?picks?/i,
      /recommendation_status/i,
      /docs\/live\.json/,
      /docs\/data\.json/,
      /git (commit|push|add)\b/,
      /anthropic/i,
      /claude/i,
    ];
    for (const pattern of forbidden) {
      expect(codeOnly, `code must not match ${pattern}`).not.toMatch(pattern);
    }
  });

  it('only ever targets dashboard-live.yml on ref main', () => {
    expect(source).toContain('dashboard-live.yml');
    expect(source).toMatch(/REF\s*=\s*'main'/);
  });
});
