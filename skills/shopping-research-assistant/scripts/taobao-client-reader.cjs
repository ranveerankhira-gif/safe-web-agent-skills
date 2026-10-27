#!/usr/bin/env node
'use strict';

const DEFAULT_PORT = 9223;
const MAX_REVIEW_PAGES = 50;

function isTrustedShoppingUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return url.protocol === 'https:' && ['taobao.com', 'tmall.com'].some((suffix) =>
      url.hostname === suffix || url.hostname.endsWith(`.${suffix}`));
  } catch {
    return false;
  }
}

function isTrustedProductUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return isTrustedShoppingUrl(rawUrl) &&
      ((url.hostname === 'detail.tmall.com' || url.hostname === 'item.taobao.com') &&
        url.pathname === '/item.htm' && /^\d+$/.test(url.searchParams.get('id') || ''));
  } catch {
    return false;
  }
}

function isTrustedCartUrl(rawUrl) {
  try {
    const url = new URL(rawUrl);
    return isTrustedShoppingUrl(rawUrl) && url.hostname === 'cart.taobao.com' && url.pathname === '/cart.htm';
  } catch {
    return false;
  }
}

function parsePort(rawPort) {
  const port = Number(rawPort || DEFAULT_PORT);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error('TAOBAO_CDP_PORT must be an integer from 1 to 65535');
  }
  return port;
}

function buildItemUrl(itemId) {
  if (!/^\d+$/.test(itemId || '')) throw new Error('open-item requires a numeric item id');
  return `https://detail.tmall.com/item.htm?id=${itemId}`;
}

function validateLoopbackWebSocketUrl(rawUrl, expectedPort) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    throw new Error('Refusing an invalid CDP WebSocket URL');
  }
  if (url.protocol !== 'ws:' || url.hostname !== '127.0.0.1' ||
      Number(url.port) !== expectedPort || url.username || url.password) {
    throw new Error('Refusing a non-loopback, credentialed, or unexpected-port CDP WebSocket');
  }
  return url.toString();
}

function requireActionConfirmation(command, args) {
  if (command === 'open-item') {
    const expected = `OPEN_ITEM:${args[1] || ''}`;
    if (args[2] !== '--confirm' || args[3] !== expected) {
      throw new Error(`open-item requires --confirm ${expected} after current-task authorization`);
    }
  } else if (command === 'add-cart') {
    if (args[3] !== '1') throw new Error('add-cart currently supports exactly quantity 1');
    const expected = `ADD_CART:${args[1] || ''}:${args[2] || ''}:1`;
    if (args[4] !== '--confirm' || args[5] !== expected) {
      throw new Error(`add-cart requires --confirm ${expected} after exact item, SKU, and quantity approval`);
    }
  } else if (command === 'close-debug') {
    if (args[1] !== '--confirm' || args[2] !== 'CLOSE_DEBUG_CLIENT') {
      throw new Error('close-debug requires --confirm CLOSE_DEBUG_CLIENT');
    }
  }
}

function selectionMatchesSku(selected = [], propPath = '') {
  const actual = selected.map((row) => `${row.pid}:${row.vid}`).sort();
  const expected = String(propPath || '').split(';').filter(Boolean).sort();
  return expected.length > 0 && actual.length === expected.length &&
    actual.every((value, index) => value === expected[index]);
}

function riskStatus(feature = {}) {
  return {
    pcIdentityRisk: String(feature.pcIdentityRisk || '').toLowerCase() === 'true',
    pcResistDetail: String(feature.pcResistDetail || '').toLowerCase() === 'true',
  };
}

function normalizeComment(row = {}) {
  const appended = row.appendedFeed && typeof row.appendedFeed === 'object'
    ? {
        content: row.appendedFeed.appendedFeedback || '',
        date: row.appendedFeed.createTime || '',
        interval: row.appendedFeed.createTimeInterval || '',
        intervalDays: row.appendedFeed.intervalDay || '',
        sellerReply: row.appendedFeed.reply || '',
      }
    : null;
  return {
    id: String(row.id || ''),
    date: row.feedbackDate || row.createTime || '',
    relativeDate: row.createTimeInterval || '',
    content: row.feedback || '',
    ratingType: String(row.rateType || ''),
    negative: String(row.rateType || '') === '-1',
    user: row.reduceUserNick || row.userNick || '',
    skuId: String(row.skuId || ''),
    sku: row.skuValueStr || '',
    sellerReply: row.reply || '',
    followUp: appended && appended.content ? appended : null,
  };
}

function normalizeRateData(data = {}, pageNum = 1) {
  const rows = Array.isArray(data.rateList) ? data.rateList : [];
  const tabs = Array.isArray(data.imprNewItemVOS)
    ? data.imprNewItemVOS.map((item) => ({
        title: item.title || '',
        count: item.fuzzyCount || item.count || '',
        rateType: item.extraInfo?.rateType || '',
      }))
    : [];
  return {
    page: pageNum,
    pageSize: rows.length,
    totalPages: Number(data.totalPage || 0),
    hasNext: String(data.hasNext || '').toLowerCase() === 'true',
    counts: {
      exact: data.total || data.feedAllCount || null,
      display: data.totalFuzzy || data.feedAllCountFuzzy || data.fuzzyRateCount || null,
      followUp: data.feedAppendCount || null,
      media: data.feedMediaCount || null,
      pictures: data.feedPicCount || null,
      videos: data.feedVideoCount || null,
    },
    tabs,
    comments: rows.map(normalizeComment),
  };
}

function classifyMtopFailure(error) {
  const ret = Array.isArray(error?.ret) ? error.ret.join(';') : '';
  const message = `${ret} ${error?.message || ''} ${String(error || '')}`;
  if (/USER_VALIDATE|RGV587|验证码|滑块|FAIL_SYS_TRAFFIC_LIMIT|被挤爆/i.test(message)) {
    return 'PLATFORM_SESSION_RESTRICTED';
  }
  if (/NEED_LOGIN|SESSION_EXPIRED|SID_INVALID|AUTH_REJECT|登录/i.test(message)) {
    return 'LOGIN_REQUIRED';
  }
  return 'CLIENT_REQUEST_FAILED';
}

function extractPriceFields(text) {
  const compact = String(text || '').replace(/\s+/g, ' ').trim();
  const prices = [...compact.matchAll(/[￥¥]\s*(\d+)(?:\s*\.\s*(\d{1,2}))?(?!\s*[\d.?])/g)]
    .map((match) => `${match[1]}${match[2] ? `.${match[2]}` : ''}`);
  return {text: compact, prices: [...new Set(prices)]};
}

class CdpClient {
  constructor(url) {
    this.url = url;
    this.ws = null;
    this.nextId = 1;
    this.pending = new Map();
  }

  async connect() {
    this.ws = new WebSocket(this.url);
    this.ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !this.pending.has(message.id)) return;
      const pending = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message || JSON.stringify(message.error)));
      else pending.resolve(message.result);
    };
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = reject;
    });
  }

  call(method, params = {}, sessionId) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP timeout: ${method}`));
      }, 25000);
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timeout); resolve(value); },
        reject: (error) => { clearTimeout(timeout); reject(error); },
      });
      this.ws.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
    });
  }

  send(method, params = {}, sessionId) {
    const id = this.nextId++;
    this.ws.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
    return id;
  }

  close() {
    this.ws?.close();
  }
}

async function discover(port) {
  const response = await fetch(`http://127.0.0.1:${port}/json/version`);
  if (!response.ok) throw new Error(`CDP discovery failed: HTTP ${response.status}`);
  const body = await response.json();
  body.webSocketDebuggerUrl = validateLoopbackWebSocketUrl(body.webSocketDebuggerUrl, port);
  return body;
}

function chooseProductTarget(targets) {
  const product = targets.find((target) => target.type === 'page' &&
    isTrustedProductUrl(target.url) &&
    !/验证码拦截/.test(target.title || ''));
  if (product) return product;
  return targets.find((target) => target.type === 'page' &&
    isTrustedProductUrl(target.url));
}

async function attach(client, target) {
  const {sessionId} = await client.call('Target.attachToTarget', {targetId: target.targetId, flatten: true});
  await client.call('Runtime.enable', {}, sessionId);
  await client.call('Page.enable', {}, sessionId);
  return sessionId;
}

async function openItem(client, targets, itemId) {
  const url = buildItemUrl(itemId);
  const target = targets.find((row) => row.type === 'page' && isTrustedCartUrl(row.url)) ||
    chooseProductTarget(targets) ||
    targets.find((row) => row.type === 'page' && isTrustedShoppingUrl(row.url));
  if (!target) return {status: 'CLIENT_PAGE_UNAVAILABLE', itemId};
  const sessionId = await attach(client, target);
  await client.call('Target.activateTarget', {targetId: target.targetId});
  await client.call('Page.bringToFront', {}, sessionId);
  await client.call('Page.navigate', {url}, sessionId);
  let state = null;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    state = await evaluate(client, sessionId,
      `({url: location.href, title: document.title, readyState: document.readyState})`);
    if (state?.readyState === 'complete') break;
  }
  if (!state || !isTrustedShoppingUrl(state.url)) {
    return {status: 'CLIENT_PAGE_UNAVAILABLE', itemId};
  }
  if (/验证码拦截/.test(state.title || '')) {
    return {status: 'PLATFORM_SESSION_RESTRICTED', itemId, url: state.url, title: state.title,
      message: 'The official client is showing an account challenge; user action is required'};
  }
  return {status: 'OPENED', itemId, url: state.url, title: state.title};
}

async function addCart(client, targets, itemId, skuId) {
  if (!/^\d+$/.test(itemId || '')) throw new Error('add-cart requires a numeric item id');
  if (!/^\d+$/.test(skuId || '')) throw new Error('add-cart requires a numeric sku id');
  const target = targets.find((row) => {
    if (row.type !== 'page' || !isTrustedProductUrl(row.url) || /验证码拦截/.test(row.title || '')) return false;
    try {
      const url = new URL(row.url);
      return url.searchParams.get('id') === itemId && url.searchParams.get('skuId') === skuId;
    } catch {
      return false;
    }
  });
  if (!target) return {status: 'EXACT_SKU_PAGE_NOT_FOUND', itemId, skuId};
  const sessionId = await attach(client, target);
  const state = await evaluate(client, sessionId, `(() => {
    const itemId = ${JSON.stringify(itemId)};
    const skuId = ${JSON.stringify(skuId)};
    const url = new URL(location.href);
    const res = window.__ICE_APP_CONTEXT__?.loaderData?.home?.data?.res;
    const props = Array.isArray(res?.skuBase?.props) ? res.skuBase.props : [];
    const skus = Array.isArray(res?.skuBase?.skus) ? res.skuBase.skus : [];
    const targetSku = skus.find(row => String(row.skuId) === skuId);
    const selected = props.flatMap(prop => (Array.isArray(prop.values) ? prop.values : [])
      .filter(value => value.selected)
      .map(value => ({property: prop.name || '', name: value.name || '',
        pid: String(prop.pid || ''), vid: String(value.vid || '')})));
    return {
      url: location.href,
      itemMatches: url.searchParams.get('id') === itemId,
      skuMatches: url.searchParams.get('skuId') === skuId,
      selected,
      propPath: targetSku?.propPath || '',
      addCartCheck: String(res?.skuCore?.sku2info?.[skuId]?.cartParam?.addCartCheck || ''),
    };
  })()`);
  if (!state?.itemMatches || !state?.skuMatches || !selectionMatchesSku(state.selected, state.propPath)) {
    return {status: 'EXACT_SKU_NOT_SELECTED', itemId, skuId, selected: state?.selected || []};
  }
  if (state.addCartCheck !== 'true') {
    return {status: 'ADD_CART_NOT_ALLOWED', itemId, skuId, selected: state.selected};
  }
  const clicked = await evaluate(client, sessionId, `(() => {
    const rows = [...document.querySelectorAll('button,[role="button"],a')]
      .map(element => {
        const text = (element.innerText || element.textContent || '').trim().replace(/\\s+/g, ' ');
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        return {element, text, rect, style};
      })
      .filter(row => row.text === '加入购物车' && row.rect.width > 0 && row.rect.height > 0 &&
        row.style.display !== 'none' && row.style.visibility !== 'hidden' && row.style.pointerEvents !== 'none')
      .sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
    if (rows.length !== 1) return {ok: false, matches: rows.length};
    rows[0].element.click();
    return {ok: true, tag: rows[0].element.tagName, text: rows[0].text};
  })()`);
  if (!clicked?.ok) return {status: 'ADD_CART_CONTROL_UNAVAILABLE', itemId, skuId,
    matches: clicked?.matches ?? null};
  await new Promise((resolve) => setTimeout(resolve, 2500));
  return {status: 'ADD_CART_CLICKED', itemId, skuId, selected: state.selected,
    control: {tag: clicked.tag, text: clicked.text}};
}

async function closeDebugClient(client) {
  const closed = new Promise((resolve) => client.ws.addEventListener('close', resolve, {once: true}));
  client.send('Browser.close');
  const outcome = await Promise.race([
    closed.then(() => 'connection_closed'),
    new Promise((resolve) => setTimeout(() => resolve('request_sent'), 5000)),
  ]);
  return {status: 'CLOSE_REQUESTED', backend: 'official_client_loopback_cdp', outcome};
}

async function evaluate(client, sessionId, expression) {
  const result = await client.call('Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  }, sessionId);
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.exception?.description ||
      result.exceptionDetails.text || 'Runtime evaluation failed');
  }
  return result.result?.value;
}

async function readStatus(client, targets) {
  const target = chooseProductTarget(targets);
  if (!target) return {status: 'CLIENT_PAGE_UNAVAILABLE', message: 'No Taobao/Tmall product page is open'};
  if (/验证码拦截/.test(target.title || '')) {
    return {status: 'PLATFORM_SESSION_RESTRICTED', url: target.url, title: target.title,
      message: 'The official client is showing an account challenge; user action is required'};
  }
  const sessionId = await attach(client, target);
  const state = await evaluate(client, sessionId, `(() => {
    const res = window.__ICE_APP_CONTEXT__?.loaderData?.home?.data?.res;
    return {
      url: location.href,
      title: document.title,
      itemId: new URL(location.href).searchParams.get('id'),
      shopName: res?.seller?.shopName || res?.seller?.sellerNick || '',
      itemTitle: res?.item?.title || document.title,
      feature: res?.feature || {},
      mtopAvailable: Boolean(window.lib?.mtop?.request),
      initialReviewCount: res?.componentsVO?.rateVO?.totalCount || null,
    };
  })()`);
  return {
    status: state.mtopAvailable ? 'READY' : 'CLIENT_CONTROL_UNAVAILABLE',
    backend: 'official_client_loopback_cdp',
    ...state,
    risk: riskStatus(state.feature),
  };
}

async function requestReviewPage(client, target, pageNum) {
  const sessionId = await attach(client, target);
  return evaluate(client, sessionId, `(async () => {
    const res = window.__ICE_APP_CONTEXT__?.loaderData?.home?.data?.res;
    const itemId = new URL(location.href).searchParams.get('id');
    if (!/^\\d+$/.test(itemId || '')) return {ok: false, status: 'CLIENT_PAGE_UNAVAILABLE'};
    if (!window.lib?.mtop?.request) return {ok: false, status: 'CLIENT_CONTROL_UNAVAILABLE'};
    try {
      const response = await window.lib.mtop.request({
        api: 'mtop.taobao.rate.detaillist.get',
        v: '6.0',
        data: {
          showTrueCount: false,
          auctionNumId: itemId,
          pageNo: ${pageNum},
          pageSize: 20,
          orderType: '',
          searchImpr: '-8',
          expression: '',
          skuVids: '',
          rateSrc: 'pc_rate_list',
          rateType: '',
        },
        isSec: '0', ecode: '1', timeout: 20000, type: 'get', dataType: 'jsonp',
      });
      return {ok: true, itemId, title: res?.item?.title || document.title,
        shopName: res?.seller?.shopName || res?.seller?.sellerNick || '',
        feature: res?.feature || {}, api: response?.api || '', ret: response?.ret || [],
        data: response?.data || {}};
    } catch (error) {
      return {ok: false, itemId, feature: res?.feature || {},
        ret: error?.ret || [], message: String(error?.stack || error?.message || error)};
    }
  })()`);
}

async function readReviews(client, targets, pageNum) {
  const target = chooseProductTarget(targets);
  if (!target) return {status: 'CLIENT_PAGE_UNAVAILABLE'};
  if (/验证码拦截/.test(target.title || '')) return {status: 'PLATFORM_SESSION_RESTRICTED'};
  const raw = await requestReviewPage(client, target, pageNum);
  if (!raw.ok) return {status: classifyMtopFailure(raw), itemId: raw.itemId || null,
    ret: raw.ret || [], message: raw.message || '', risk: riskStatus(raw.feature)};
  return {
    status: 'OK',
    backend: 'official_client_loopback_cdp_mtop',
    itemId: raw.itemId,
    title: raw.title,
    shopName: raw.shopName,
    risk: riskStatus(raw.feature),
    api: raw.api,
    ret: raw.ret,
    ...normalizeRateData(raw.data, pageNum),
  };
}

async function auditReviews(client, targets) {
  const first = await readReviews(client, targets, 1);
  if (first.status !== 'OK') return first;
  const pages = Math.max(1, Math.min(MAX_REVIEW_PAGES, first.totalPages || 1));
  const comments = [...first.comments];
  for (let page = 2; page <= pages; page += 1) {
    const result = await readReviews(client, targets, page);
    if (result.status !== 'OK') {
      return {...first, status: 'PARTIAL', readPages: page - 1, failedPage: page,
        failure: result, negativeReviews: comments.filter((row) => row.negative),
        followUps: comments.filter((row) => row.followUp)};
    }
    comments.push(...result.comments);
  }
  const unique = [...new Map(comments.map((row) => [row.id || JSON.stringify(row), row])).values()];
  return {
    status: 'OK',
    backend: first.backend,
    itemId: first.itemId,
    title: first.title,
    shopName: first.shopName,
    risk: first.risk,
    counts: first.counts,
    tabs: first.tabs,
    readPages: pages,
    readCount: unique.length,
    negativeCountObserved: unique.filter((row) => row.negative).length,
    followUpCountObserved: unique.filter((row) => row.followUp).length,
    negativeReviews: unique.filter((row) => row.negative),
    followUps: unique.filter((row) => row.followUp),
    sampleOtherReviews: unique.filter((row) => !row.negative && !row.followUp).slice(0, 5),
  };
}

async function readCartPrice(client, targets, itemId) {
  if (!/^\d+$/.test(itemId || '')) throw new Error('cart-price requires a numeric item id');
  let target = targets.find((row) => row.type === 'page' && isTrustedCartUrl(row.url));
  let expectedTitle = '';
  if (!target) target = chooseProductTarget(targets);
  if (!target) return {status: 'CLIENT_PAGE_UNAVAILABLE', itemId};
  const sessionId = await attach(client, target);
  if (!isTrustedCartUrl(target.url)) {
    expectedTitle = await evaluate(client, sessionId,
      `window.__ICE_APP_CONTEXT__?.loaderData?.home?.data?.res?.item?.title || ''`);
    await client.call('Page.navigate', {url: 'https://cart.taobao.com/cart.htm'}, sessionId);
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 500));
      const ready = await evaluate(client, sessionId,
        `({url: location.href, readyState: document.readyState})`);
      if (/^https:\/\/cart\.taobao\.com\/cart\.htm/.test(ready?.url || '') &&
          ready?.readyState === 'complete') break;
      if (attempt === 29) return {status: 'CART_PAGE_UNAVAILABLE', itemId};
    }
  }
  const row = await evaluate(client, sessionId, `(() => {
    const itemId = ${JSON.stringify(itemId)};
    const expectedTitle = ${JSON.stringify(expectedTitle)};
    const candidates = [...document.querySelectorAll('[href], [data-id], [data-item-id], a, div, span')].filter(element => {
      const href = element.getAttribute('href') || '';
      return href.includes(itemId) || element.getAttribute('data-id') === itemId ||
        element.getAttribute('data-item-id') === itemId ||
        (expectedTitle && (element.innerText || element.textContent || '').trim() === expectedTitle);
    });
    for (const candidate of candidates) {
      let element = candidate;
      for (let depth = 0; element && element !== document.body && depth < 12; depth += 1, element = element.parentElement) {
        const text = (element.innerText || '').trim();
        if (text.length >= 20 && text.length <= 2500 && /[￥¥]\\s*\\d/.test(text)) {
          return {found: true, text};
        }
      }
    }
    return {found: false};
  })()`);
  if (!row?.found) return {status: 'TARGET_ITEM_NOT_IN_CART', itemId};
  return {status: 'OK', itemId, backend: 'official_client_cart_dom', ...extractPriceFields(row.text)};
}

async function main(argv = process.argv.slice(2)) {
  const command = argv[0] || 'status';
  requireActionConfirmation(command, argv);
  const port = parsePort(process.env.TAOBAO_CDP_PORT);
  const discovery = await discover(port);
  const client = new CdpClient(discovery.webSocketDebuggerUrl);
  await client.connect();
  try {
    const {targetInfos} = await client.call('Target.getTargets');
    let output;
    if (command === 'status') output = await readStatus(client, targetInfos);
    else if (command === 'open-item') output = await openItem(client, targetInfos, argv[1]);
    else if (command === 'add-cart') output = await addCart(client, targetInfos, argv[1], argv[2]);
    else if (command === 'close-debug') output = await closeDebugClient(client);
    else if (command === 'reviews') {
      const page = Number(argv[1] || 1);
      if (!Number.isInteger(page) || page < 1 || page > MAX_REVIEW_PAGES) {
        throw new Error(`reviews page must be an integer from 1 to ${MAX_REVIEW_PAGES}`);
      }
      output = await readReviews(client, targetInfos, page);
    } else if (command === 'audit') output = await auditReviews(client, targetInfos);
    else if (command === 'cart-price') output = await readCartPrice(client, targetInfos, argv[1]);
    else throw new Error('Usage: taobao-client-reader.cjs status | open-item <itemId> --confirm OPEN_ITEM:<itemId> | add-cart <itemId> <skuId> 1 --confirm ADD_CART:<itemId>:<skuId>:1 | reviews [page] | audit | cart-price <itemId> | close-debug --confirm CLOSE_DEBUG_CLIENT');
    process.stdout.write(`${JSON.stringify(output, null, 2)}\n`);
  } finally {
    client.close();
  }
}

module.exports = {
  isTrustedShoppingUrl,
  isTrustedProductUrl,
  isTrustedCartUrl,
  parsePort,
  buildItemUrl,
  validateLoopbackWebSocketUrl,
  requireActionConfirmation,
  selectionMatchesSku,
  riskStatus,
  normalizeComment,
  normalizeRateData,
  classifyMtopFailure,
  extractPriceFields,
};

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`${error.stack || error.message || String(error)}\n`);
    process.exitCode = 1;
  });
}


