'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
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
} = require('../scripts/taobao-client-reader.cjs');

test('allows only HTTPS Taobao and Tmall hosts', () => {
  assert.equal(isTrustedShoppingUrl('https://detail.tmall.com/item.htm?id=1'), true);
  assert.equal(isTrustedShoppingUrl('https://cart.taobao.com/cart.htm'), true);
  assert.equal(isTrustedShoppingUrl('http://detail.tmall.com/item.htm?id=1'), false);
  assert.equal(isTrustedShoppingUrl('https://tmall.com.evil.example/item'), false);
});

test('accepts only exact trusted product and cart pages', () => {
  assert.equal(isTrustedProductUrl('https://detail.tmall.com/item.htm?id=1'), true);
  assert.equal(isTrustedProductUrl('https://evil.example/?next=detail.tmall.com/item.htm?id=1'), false);
  assert.equal(isTrustedProductUrl('https://detail.tmall.com/item.htm?id=not-numeric'), false);
  assert.equal(isTrustedCartUrl('https://cart.taobao.com/cart.htm'), true);
  assert.equal(isTrustedCartUrl('https://evil.example/cart.taobao.com/cart.htm'), false);
});

test('validates the loopback CDP port number', () => {
  assert.equal(parsePort('9223'), 9223);
  assert.throws(() => parsePort('0'));
  assert.throws(() => parsePort('not-a-port'));
});

test('validates the parsed CDP WebSocket origin and exact port', () => {
  assert.equal(
    validateLoopbackWebSocketUrl('ws://127.0.0.1:9223/devtools/browser/abc', 9223),
    'ws://127.0.0.1:9223/devtools/browser/abc',
  );
  assert.throws(() => validateLoopbackWebSocketUrl('ws://127.0.0.1:9223@evil.example/devtools', 9223));
  assert.throws(() => validateLoopbackWebSocketUrl('ws://127.0.0.1:9224/devtools', 9223));
  assert.throws(() => validateLoopbackWebSocketUrl('wss://127.0.0.1:9223/devtools', 9223));
});

test('requires exact command-level confirmation for client mutations', () => {
  assert.doesNotThrow(() => requireActionConfirmation('open-item', [
    'open-item', '123', '--confirm', 'OPEN_ITEM:123',
  ]));
  assert.throws(() => requireActionConfirmation('open-item', ['open-item', '123']));
  assert.doesNotThrow(() => requireActionConfirmation('add-cart', [
    'add-cart', '123', '456', '1', '--confirm', 'ADD_CART:123:456:1',
  ]));
  assert.throws(() => requireActionConfirmation('add-cart', [
    'add-cart', '123', '456', '2', '--confirm', 'ADD_CART:123:456:2',
  ]));
  assert.throws(() => requireActionConfirmation('add-cart', [
    'add-cart', '123', '456', '1', '--confirm', 'ADD_CART:123:999:1',
  ]));
  assert.doesNotThrow(() => requireActionConfirmation('close-debug', [
    'close-debug', '--confirm', 'CLOSE_DEBUG_CLIENT',
  ]));
  assert.throws(() => requireActionConfirmation('close-debug', ['close-debug']));
});

test('builds only a fixed Tmall item URL from a numeric item id', () => {
  assert.equal(buildItemUrl('971945490895'), 'https://detail.tmall.com/item.htm?id=971945490895');
  assert.throws(() => buildItemUrl('https://evil.example/'));
  assert.throws(() => buildItemUrl('1&redirect=https://evil.example/'));
});

test('requires the selected property path to match the exact sku', () => {
  const selected = [{pid: '1627207', vid: '42038934813'}, {pid: '20509', vid: '28315'}];
  assert.equal(selectionMatchesSku(selected, '1627207:42038934813;20509:28315'), true);
  assert.equal(selectionMatchesSku(selected, '1627207:42038934814;20509:28315'), false);
  assert.equal(selectionMatchesSku(selected.slice(0, 1), '1627207:42038934813;20509:28315'), false);
});

test('normalizes risk flags without making them a zero-review claim', () => {
  assert.deepEqual(riskStatus({pcIdentityRisk: 'true', pcResistDetail: 'false'}), {
    pcIdentityRisk: true,
    pcResistDetail: false,
  });
});

test('preserves negative and follow-up review text', () => {
  const result = normalizeComment({
    id: 7,
    feedback: '面料粗糙',
    feedbackDate: '2026年6月21日',
    rateType: '-1',
    skuValueStr: '卡其色；M',
    appendedFeed: {
      appendedFeedback: '里面还有别人用过的纸巾',
      createTime: '6月28日',
      intervalDay: '7',
    },
  });
  assert.equal(result.negative, true);
  assert.equal(result.content, '面料粗糙');
  assert.equal(result.followUp.content, '里面还有别人用过的纸巾');
  assert.equal(result.followUp.intervalDays, '7');
});

test('normalizes exact, fuzzy, and follow-up counts separately', () => {
  const result = normalizeRateData({
    total: '100',
    totalFuzzy: '100+',
    feedAppendCount: '3',
    totalPage: '7',
    hasNext: 'true',
    rateList: [{id: '1', feedback: '正文', rateType: '1'}],
  }, 1);
  assert.deepEqual(result.counts, {
    exact: '100',
    display: '100+',
    followUp: '3',
    media: null,
    pictures: null,
    videos: null,
  });
  assert.equal(result.comments[0].content, '正文');
});

test('maps account challenges to a restricted session', () => {
  assert.equal(classifyMtopFailure({ret: ['FAIL_SYS_USER_VALIDATE']}), 'PLATFORM_SESSION_RESTRICTED');
  assert.equal(classifyMtopFailure({ret: ['FAIL_SYS_SESSION_EXPIRED']}), 'LOGIN_REQUIRED');
  assert.equal(classifyMtopFailure(new Error('network down')), 'CLIENT_REQUEST_FAILED');
});

test('extracts complete currency amounts but not fuzzy values', () => {
  assert.deepEqual(extractPriceFields('店铺优惠后 ￥ 339 划线价 ￥399'), {
    text: '店铺优惠后 ￥ 339 划线价 ￥399',
    prices: ['339', '399'],
  });
  assert.deepEqual(extractPriceFields('平台加补后 ￥ 250 . 8 优惠前 ￥ 339'), {
    text: '平台加补后 ￥ 250 . 8 优惠前 ￥ 339',
    prices: ['250.8', '339'],
  });
  assert.deepEqual(extractPriceFields('￥3??'), {text: '￥3??', prices: []});
});


