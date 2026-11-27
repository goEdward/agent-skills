// 多 Profile 选择的纯函数，便于在不连接浏览器的情况下回归测试。

export function splitConfigList(value) {
  return String(value || '')
    .split(',')
    .map(item => item.trim())
    .filter(Boolean);
}

export function makeProfileSelectionConfig(readPreference) {
  const legacyAnchor = readPreference('WEB_ACCESS_CHROME_ANCHOR_URL');
  const anchorUrls = splitConfigList(
    readPreference('WEB_ACCESS_CHROME_ANCHOR_URLS') || legacyAnchor || ''
  );
  const excludeUrls = splitConfigList(readPreference('WEB_ACCESS_CHROME_EXCLUDE_URLS'));
  return {
    profileDir: readPreference('WEB_ACCESS_BROWSER_PROFILE') ||
      readPreference('WEB_ACCESS_CHROME_PROFILE') || null,
    anchorUrls,
    excludeUrls,
  };
}

export function profileSelectionKey(config) {
  return JSON.stringify({
    profileDir: config.profileDir || '',
    anchorUrls: config.anchorUrls || [],
    excludeUrls: config.excludeUrls || [],
  });
}

export function extractProfilePath(versionBody) {
  const lines = String(versionBody || '').split(/\r?\n/);
  for (const line of lines) {
    const match = line.trim().match(/^(?:Profile\s*Path|个人资料路径)\s*[:：\t ]+(.+)$/i);
    if (match) return match[1].trim();
  }
  return '';
}

function normalizePath(value) {
  return String(value || '')
    .replaceAll('\\', '/')
    .trim()
    .replace(/\/+$/, '');
}

function pathBase(value) {
  const normalized = normalizePath(value);
  return normalized.slice(normalized.lastIndexOf('/') + 1).toLowerCase();
}

export function profilePathMatches(actualPath, expectedProfile) {
  const actual = pathBase(actualPath);
  const expected = pathBase(expectedProfile);
  if (!actual || !expected) return false;
  // 允许用户写 Profile8，也允许 Chrome 实际目录名 Profile 8；仍保持
  // Profile 8 与 Profile 80 的严格区分。
  return actual === expected || actual.replaceAll(' ', '') === expected.replaceAll(' ', '');
}

export function rankProfileContexts(contexts, contextStats) {
  return [...contexts].sort((a, b) => {
    const left = contextStats.get(a) || {};
    const right = contextStats.get(b) || {};
    return (right.positive || 0) - (left.positive || 0) ||
      (left.negative || 0) - (right.negative || 0) ||
      (right.pages || 0) - (left.pages || 0) ||
      String(a).localeCompare(String(b));
  });
}

// Runtime Profile routing: injected CDP operations preserve the authenticated parent's lifecycle.
export function createProfileRouter({
  sendCDP, ensureSession, managedTabs, registerManagedTarget, closeManagedTargets,
  forgetSession, touchTab, persistManagedTabs, readConfigPreference, getBrowserId,
  internalOwner,
}) {
  const INTERNAL_PROBE_OWNER = internalOwner;
  let preferredBrowserContextId;
  let activeProfileConfigKey;
  let profileBridgeTargetId = null;
  let profileBridgeContextId = null;
  function foregroundFallbackAllowed() {
    return /^(1|true|yes|on)$/i.test(readConfigPreference('WEB_ACCESS_ALLOW_FOREGROUND_FALLBACK') || '');
  }
  function currentProfileSelectionConfig() {
    const config = makeProfileSelectionConfig(readConfigPreference);
    const key = profileSelectionKey(config);
    if (activeProfileConfigKey !== undefined && activeProfileConfigKey !== key) {
      resetPreferredBrowserContext('Profile 配置已变化');
    }
    activeProfileConfigKey = key;
    return config;
  }

  function resetPreferredBrowserContext(reason = '') {
    if (preferredBrowserContextId !== undefined && reason) {
      console.log(`[CDP Proxy] 清除缓存的 Profile 上下文（${reason}）`);
    }
    preferredBrowserContextId = undefined;
    if (/配置已变化/.test(reason) && profileBridgeTargetId) {
      const entry = managedTabs.get(profileBridgeTargetId);
      if (entry) entry.kind = 'profile-probe';
      persistManagedTabs();
      profileBridgeTargetId = null;
      profileBridgeContextId = null;
    }
  }

  function matchesAnchorUrl(pageUrl, anchor) {
    try {
      const parsed = new URL(pageUrl);
      const normalized = anchor.trim().replace(/^https?:\/\//, '').replace(/^\/+|\/+$/g, '');
      const slash = normalized.indexOf('/');
      const host = (slash === -1 ? normalized : normalized.slice(0, slash)).toLowerCase();
      const path = slash === -1 ? '' : `/${normalized.slice(slash + 1)}`;
      const hostname = parsed.hostname.toLowerCase();
      const hostMatched = hostname === host || hostname.endsWith(`.${host}`);
      return hostMatched && (!path || parsed.pathname.startsWith(path));
    } catch {
      return false;
    }
  }

  async function validatedCreatableContext(browserContextId, reason) {
    if (!browserContextId) {
      preferredBrowserContextId = null;
      return undefined;
    }

    const direct = await sendCDP('Target.createTarget', {
      url: 'about:blank', background: true, browserContextId,
    });
    if (!direct.error) {
      registerManagedTarget(direct.result.targetId, INTERNAL_PROBE_OWNER, 'profile-probe');
      await closeManagedTargets([direct.result.targetId]);
      preferredBrowserContextId = browserContextId;
      return browserContextId;
    }

    // Chrome may expose its attached Profile as a context id while requiring
    // Target.createTarget to omit that id because it is the connection's default.
    const fallback = await sendCDP('Target.createTarget', { url: 'about:blank', background: true });
    if (fallback.error) throw new Error(fallback.error.message || '无法验证浏览器 Profile');
    registerManagedTarget(fallback.result.targetId, INTERNAL_PROBE_OWNER, 'profile-probe');
    try {
      const info = await sendCDP('Target.getTargetInfo', { targetId: fallback.result.targetId });
      const fallbackContextId = info.result?.targetInfo?.browserContextId || '';
      if (fallbackContextId === browserContextId) {
        preferredBrowserContextId = null;
        console.log(`[CDP Proxy] 浏览器上下文已定位（${reason}，默认连接上下文）`);
        return undefined;
      }
    } finally {
      await closeManagedTargets([fallback.result.targetId]);
    }
    throw new Error(`profile_ambiguous：已定位的浏览器上下文不可创建 tab（${reason}），拒绝降级到其他 Profile。`);
  }

  async function openTabFromExistingContext(browserContextId, url, { register = true, preferredSourceId = null } = {}) {
    const before = await sendCDP('Target.getTargets');
    const pages = before.result?.targetInfos?.filter(t => t.type === 'page') || [];
    const candidates = pages.filter(t => (t.browserContextId || '') === browserContextId);
    const orderedSources = [];
    const preferred = preferredSourceId ? candidates.find(t => t.targetId === preferredSourceId) : null;
    if (preferred) orderedSources.push(preferred);
    const httpSource = candidates.find(t => t.targetId !== preferred?.targetId && /^https?:/i.test(t.url || ''));
    if (httpSource) orderedSources.push(httpSource);
    const firstSource = candidates.find(t => !orderedSources.includes(t));
    if (firstSource) orderedSources.push(firstSource);
    if (!orderedSources.length) return null;

    let targetId = null;
    for (const source of orderedSources) {
      const attached = await sendCDP('Target.attachToTarget', { targetId: source.targetId, flatten: true });
      const sourceSessionId = attached.result?.sessionId;
      if (!sourceSessionId) continue;

      const knownTargets = new Set(pages.map(t => t.targetId));
      try {
        const opened = await sendCDP('Runtime.evaluate', {
          expression: `window.open('about:blank', '_blank'); true`,
          returnByValue: true,
          userGesture: true,
        }, sourceSessionId);
        if (opened.error) continue;
      } finally {
        await sendCDP('Target.detachFromTarget', { sessionId: sourceSessionId });
      }

      for (let i = 0; i < 20 && !targetId; i++) {
        await new Promise(resolve => setTimeout(resolve, 100));
        const after = await sendCDP('Target.getTargets');
        const newPages = (after.result?.targetInfos || []).filter(t =>
          t.type === 'page' && !knownTargets.has(t.targetId)
        );
        const created = newPages.find(t => t.openerId === source.targetId) ||
          (newPages.filter(t => (t.browserContextId || '') === browserContextId).length === 1
            ? newPages.find(t => (t.browserContextId || '') === browserContextId)
            : null);
        if (created) targetId = created.targetId;
      }
      if (targetId) break;
    }
    if (!targetId) return null;

    if (register) registerManagedTarget(targetId, INTERNAL_PROBE_OWNER, 'profile-probe');
    if (url && url !== 'about:blank') {
      const targetSessionId = await ensureSession(targetId);
      const navigated = await sendCDP('Page.navigate', { url }, targetSessionId);
      if (navigated.error) {
        if (register) await closeManagedTargets([targetId]);
        else await sendCDP('Target.closeTarget', { targetId });
        return null;
      }
    }
    return targetId;
  }

  async function liveProfileBridge(browserContextId) {
    if (!profileBridgeTargetId || profileBridgeContextId !== browserContextId) return null;
    const info = await sendCDP('Target.getTargetInfo', { targetId: profileBridgeTargetId });
    const targetInfo = info.result?.targetInfo;
    if (!info.error && targetInfo?.type === 'page' &&
        (targetInfo.browserContextId || '') === (browserContextId || '')) {
      touchTab(profileBridgeTargetId);
      return profileBridgeTargetId;
    }
    forgetSession(profileBridgeTargetId);
    managedTabs.delete(profileBridgeTargetId);
    persistManagedTabs();
    profileBridgeTargetId = null;
    profileBridgeContextId = null;
    return null;
  }

  async function probeProfileContext(browserContextId, profileDir) {
    const versionUrl = getBrowserId() === 'edge' ? 'edge://version' : 'chrome://version';
    const probeContextId = browserContextId || '';
    let targetId;
    let profileMatched = false;

    // Chrome 的多 Profile context 在至少保留一个由该 context 创建的页面时
    // 更稳定。桥接页只保存上下文，不承载任务内容，也不会被健康检查导航。
    if (await liveProfileBridge(probeContextId)) return true;

    const params = { url: versionUrl, background: true };
    if (probeContextId) params.browserContextId = probeContextId;

    const direct = await sendCDP('Target.createTarget', params);
    if (!direct.error) {
      targetId = direct.result.targetId;
    } else if (probeContextId) {
      // Chrome 151+ 可能会在 Target.getTargets 中列出已打开的用户 Profile，
      // 却暂时拒绝把 browserContextId 传给 Target.createTarget。先尝试
      // 不带 context id 的真正后台创建；只有确认仍落在目标上下文时才接受，
      // 避免误落到工作 Profile，也避免为了探测而调用会抢焦点的 window.open。
      const fallback = await sendCDP('Target.createTarget', { url: versionUrl, background: true });
      if (!fallback.error) {
        const info = await sendCDP('Target.getTargetInfo', { targetId: fallback.result.targetId });
        const fallbackContextId = info.result?.targetInfo?.browserContextId || '';
        if (fallbackContextId === probeContextId) {
          targetId = fallback.result.targetId;
        } else {
          registerManagedTarget(fallback.result.targetId, INTERNAL_PROBE_OWNER, 'profile-probe');
          await closeManagedTargets([fallback.result.targetId]);
        }
      }
      if (!targetId) {
        // 最后的兼容路径仅在浏览器拒绝所有后台 Target.createTarget 方式时
        // 使用。保留这一个桥接页后，后续任务不再反复走该兼容路径。
        if (!foregroundFallbackAllowed()) return false;
        targetId = await openTabFromExistingContext(probeContextId, versionUrl);
        if (!targetId) return false;
      }
    } else {
      return false;
    }

    registerManagedTarget(targetId, INTERNAL_PROBE_OWNER, 'profile-probe');
    try {
      const sid = await ensureSession(targetId);
      let body = '';
      let profilePath = '';
      for (let i = 0; i < 20 && !profilePath; i++) {
        await new Promise(resolve => setTimeout(resolve, 100));
        const result = await sendCDP('Runtime.evaluate', {
          expression: 'document.body?.innerText || ""',
          returnByValue: true,
        }, sid);
        body = result.result?.result?.value || '';
        profilePath = extractProfilePath(body);
      }
      profileMatched = profilePathMatches(profilePath, profileDir);
      return profileMatched;
    } finally {
      // 显式 Profile context 的验证页保留为桥接页。若它由 background:true
      // 创建，整个过程不抢焦点；只有 Chrome 拒绝后台创建时才会首次回退。
      if (probeContextId && profileMatched) {
        profileBridgeTargetId = targetId;
        profileBridgeContextId = probeContextId;
        registerManagedTarget(targetId, INTERNAL_PROBE_OWNER, 'profile-bridge');
        try {
          const sid = await ensureSession(targetId);
          await sendCDP('Page.navigate', { url: 'about:blank' }, sid);
        } catch { /* 桥接页仍可由 liveProfileBridge 在下次使用前重新验证 */ }
        console.log(`[CDP Proxy] 已保留后台 Profile 桥接页：${probeContextId}`);
      } else {
        await closeManagedTargets([targetId]);
      }
    }
  }

  let profileResolutionPromise = null;

  async function browserContextForProfile() {
    const config = currentProfileSelectionConfig();
    if (preferredBrowserContextId !== undefined) return preferredBrowserContextId || undefined;
    if (!profileResolutionPromise) {
      profileResolutionPromise = resolveBrowserContextForProfile(config)
        .finally(() => { profileResolutionPromise = null; });
    }
    return profileResolutionPromise;
  }

  async function resolveBrowserContextForProfile(config) {
    const profileDir = config.profileDir;

    const targets = await sendCDP('Target.getTargets');
    const contexts = [...new Set(targets.result.targetInfos
      .filter(t => t.type === 'page')
      .map(t => t.browserContextId || '')
    )];

    // 标签页 URL 只用于排序候选上下文；配置了 Profile 目录时，最终必须
    // 通过 chrome://version 的 Profile Path 验证，不能仅凭某个网址选中。
    const pages = targets.result.targetInfos.filter(t => t.type === 'page');
    const contextStats = new Map(contexts.map(id => [id, { positive: 0, negative: 0, pages: 0 }]));
    for (const page of pages) {
      const stats = contextStats.get(page.browserContextId || '');
      if (!stats) continue;
      stats.pages += 1;
      stats.positive += config.anchorUrls.filter(anchor => matchesAnchorUrl(page.url || '', anchor)).length;
      stats.negative += config.excludeUrls.filter(anchor => matchesAnchorUrl(page.url || '', anchor)).length;
    }

    const rankedContexts = rankProfileContexts(contexts, contextStats);

    if (profileDir) {
      const probeContexts = [...rankedContexts];
      // 现有页面可能没有 browserContextId；仍要验证 DevToolsActivePort
      // 所连接的默认上下文，它通常就是当前打开的 Profile。
      if (!probeContexts.includes('')) probeContexts.push('');
      for (const browserContextId of probeContexts) {
        if (await probeProfileContext(browserContextId, profileDir)) {
          preferredBrowserContextId = browserContextId || null;
          console.log(`[CDP Proxy] 浏览器 Profile 已验证：${profileDir}`);
          return browserContextId || undefined;
        }
      }
      throw new Error(
        `配置的浏览器 Profile "${profileDir}" 当前未加载。请先打开该 Profile 的浏览器窗口后重试。`
      );
    }

    let selectedContextId;
    let selectionReason;
    const positiveContexts = rankedContexts
      .map(id => [id, contextStats.get(id)])
      .filter(([, stats]) => stats.positive > 0)
      .sort((a, b) => b[1].positive - a[1].positive || a[1].negative - b[1].negative);
    if (positiveContexts.length === 1 ||
        (positiveContexts.length > 1 && positiveContexts[0][1].positive > positiveContexts[1][1].positive)) {
      selectedContextId = positiveContexts[0][0];
      selectionReason = `正向锚点 ${positiveContexts[0][1].positive} 个`;
    } else if (positiveContexts.length === 0 && config.excludeUrls.length) {
      const excludedContexts = new Set(
        [...contextStats.entries()].filter(([, stats]) => stats.negative > 0).map(([id]) => id)
      );
      const remaining = contexts.filter(id => !excludedContexts.has(id));
      if (excludedContexts.size > 0 && remaining.length === 1) {
        selectedContextId = remaining[0];
        selectionReason = `排除其他 Profile 的 ${excludedContexts.size} 个上下文`;
      }
    }

    if (selectedContextId !== undefined) {
      const browserContextId = await validatedCreatableContext(selectedContextId, selectionReason);
      if (browserContextId) console.log(`[CDP Proxy] 浏览器上下文已定位（${selectionReason}）`);
      return browserContextId;
    }

    if (contexts.length <= 1) {
      return validatedCreatableContext(contexts[0] || '', '唯一浏览器上下文');
    }

    throw new Error(
      'profile_ambiguous：检测到多个浏览器 Profile，但锚点和 Profile 配置都无法唯一定位。' +
      '请配置 WEB_ACCESS_CHROME_ANCHOR_URLS / WEB_ACCESS_CHROME_EXCLUDE_URLS 或 WEB_ACCESS_BROWSER_PROFILE。'
    );
  }

  function isStaleBrowserContextError(error) {
    const message = String(error?.message || error || '');
    return /browser context.*not found|failed to find browser context|no browser context with id/i.test(message);
  }

  async function createTargetForCurrentProfile(params = {}) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const browserContextId = await browserContextForProfile();
      const createParams = { ...params };
      if (browserContextId) createParams.browserContextId = browserContextId;
      const resp = await sendCDP('Target.createTarget', createParams);
      if (!resp.error) return resp;

      const error = new Error(resp.error.message || '创建 tab 失败');
      if (attempt === 0 && isStaleBrowserContextError(error)) {
        resetPreferredBrowserContext('Chrome 报告上下文已失效');
        console.log('[CDP Proxy] 浏览器上下文已失效，正在重新定位 Profile 并重试');
        continue;
      }
      if (browserContextId && isStaleBrowserContextError(error)) {
        // Chrome 151+ 某些场景会拒绝把已打开用户 Profile 的 browserContextId 传给 Target.createTarget。
        // 先尝试不带 browserContextId 的后台创建并严格核对落入同一已验证上下文；若仍不匹配且用户允许前台桥接，
        // 则从已验证上下文的桥接页/已有页打开，绝不降级到其他 Profile。
        const fallback = await sendCDP('Target.createTarget', { ...params });
        if (!fallback.error && fallback.result?.targetId) {
          const info = await sendCDP('Target.getTargetInfo', { targetId: fallback.result.targetId });
          const fallbackContextId = info.result?.targetInfo?.browserContextId || '';
          if (fallbackContextId === browserContextId) {
            return fallback;
          }
          registerManagedTarget(fallback.result.targetId, INTERNAL_PROBE_OWNER, 'profile-probe');
          await closeManagedTargets([fallback.result.targetId]);
        }
        if (foregroundFallbackAllowed()) {
          const openedId = await openTabFromExistingContext(browserContextId, params.url || 'about:blank', {
            register: false,
            preferredSourceId: profileBridgeTargetId,
          });
          if (openedId) {
            const info = await sendCDP('Target.getTargetInfo', { targetId: openedId });
            const openedContextId = info.result?.targetInfo?.browserContextId || '';
            if (openedContextId === browserContextId) {
              return { result: { targetId: openedId } };
            }
            await sendCDP('Target.closeTarget', { targetId: openedId });
          }
        }
      }
      throw error;
    }
    throw new Error('创建 tab 失败');
  }


  return { createTarget: createTargetForCurrentProfile, reset: resetPreferredBrowserContext };
}
