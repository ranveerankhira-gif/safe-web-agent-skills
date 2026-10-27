# 评论访问与差评读取

当具体商品的差评、追评、负面问答或店铺售后无法从普通抓取直接读取时，使用本流程。目标是读取平台正常界面中真实可见的内容，不是破解评论接口。

## 访问顺序

### 淘宝与天猫

1. `official_client`：直接使用已经登录的淘宝官方桌面客户端，按 [淘宝与天猫官方客户端查验](taobao-tmall-official-client.md) 打开目标商品、全部评价和追评，并核对目标变体价格。不要先开匿名浏览器或 `taobao-login` 专属浏览器。
2. `external_source`：同时查当前 SKU、型号、店铺的公开独立体验、投诉和消费者权益来源。外部内容只能补证，不能伪装成平台评论全集。
3. 客户端无法安全控制或页面组件未渲染时，分别记 `CLIENT_CONTROL_UNAVAILABLE` 或 `CLIENT_PAGE_UNAVAILABLE`。不得自动绕回专属浏览器；只有用户在当前任务中明确要求例外时，才可使用其他登录会话。

### 其他平台

1. `public_http`：检查桌面商品页、移动商品页、公开评论页、页面内嵌数据和正常评论筛选。只把当前具体 SKU、目标变体和目标店铺的内容计入覆盖。
2. `anonymous_browser`：静态请求返回 401/403/444、业务风控码、空壳页面或只有摘要时，打开独立临时浏览器。先获取页面状态，再按可见控件进入“评价”，选择“差评/中差评/只看当前商品”，展开追评与负面问答，逐页或滚动读取；页面变化后重新获取状态。
3. `authorized_session`：匿名界面仍受登录限制时，只有在用户明确授权当前任务使用已配置专用会话后，才可用相应的只读工具打开商品和评论页。不得把 Playwright 接到该会话，不得读取账号中心、订单、地址、消息或支付信息。
4. `external_source`：同时查当前 SKU、型号、店铺的公开独立体验、投诉和消费者权益来源。

所有允许路径均失败后才记 `BLOCKED`。接口失败但尚未尝试适用平台的正常页面路径时，调查尚未完成，不能向用户说“查不了”。

没有安装浏览器工具、扩展未连接或没有已授权会话时，记录 `unavailable` 或 `not_authorized`，继续其他允许路径。不要临时安装工具、读取默认浏览器 Cookie 或要求用户提交完整截图来掩盖缺口。

## 页面读取标准

- 先确认商品标题、SKU/货号、颜色尺码等变体，再开始计数；系列页混入其他变体时分别标记。
- 记录页面宣称的负面数量 `reported_negative_count`、实际可展开的 `accessible_negative_count` 和逐条已读的 `read_negative_count`。页面没有显示总数时前者为 `null`，不能填零。
- 所有可展开内容都读完且 `read_negative_count == accessible_negative_count`，才算已完成当前可访问范围。分页上限、折叠截断、仅登录可见、变体混评或追评未展开都进入 `limitations`。
- 对每条负面内容保留原意摘要、日期或未知、变体或未知、问题类型、是否追评、图片/视频证据、商家回复和来源 URL。不要保存用户名、头像、账号标识或其他无关个人信息。
- 页面显示“0 条差评”只能证明该筛选在本次访问中返回零条；必须同时记录筛选状态和访问模式，不能推断商品没有问题。

## 访问记录

新记录使用 `schema_version: 3`。每个最终候选的 `review_coverage` 除计数字段外，还要包含：

```json
{
  "access_mode": "anonymous_browser",
  "access_result": "complete",
  "reported_negative_count": 3,
  "accessible_negative_count": 3,
  "read_negative_count": 3,
  "negative_reviews": [],
  "reader_evidence": {
    "backend": "browser",
    "command": "visible negative-review filter and pagination",
    "captured_at": "2026-10-19T10:00:00+08:00",
    "source_url": "https://shop.example/item/sku"
  },
  "filter_verified": true,
  "pages_read": 1,
  "exhausted": true,
  "adapter_version": "manual-browser/1",
  "error_code": null,
  "limitations": [],
  "source_urls": ["https://shop.example/item/sku"],
  "access_attempts": [
    {
      "mode": "public_http",
      "result": "blocked",
      "source_url": "https://shop.example/item/sku",
      "detail": "评论接口返回业务风控，商品页本身可读"
    },
    {
      "mode": "anonymous_browser",
      "result": "complete",
      "source_url": "https://shop.example/item/sku",
      "detail": "通过页面差评筛选与分页读完可见负面正文"
    }
  ]
}
```

示例中的 `negative_reviews` 仅为缩写；实际长度必须等于 `read_negative_count`。淘宝/天猫字段采集见 [淘宝与天猫官方客户端查验](taobao-tmall-official-client.md)，京东适配器字段见 [OpenCLI 京东评论读取器](opencli-review-reader.md)。`complete` 要求 `filter_verified=true`、`pages_read>=1`、`exhausted=true`、`error_code=null`。失败、登录受限、验证码或页面改版必须写非空 `error_code`，不能把 `[]`、缺少评论卡片或接口空响应当成零差评。

`access_mode` 表示取得当前覆盖数字的最强路径，可为 `public_http`、`anonymous_browser`、`official_client`、`authorized_session`、`external_source` 或 `none`；`access_result` 可为 `complete`、`partial`、`blocked` 或 `unavailable`。`access_attempts.result` 还可使用 `not_authorized`。

使用 `official_client` 或 `authorized_session` 时，尝试记录必须含 `authorization: "current_task"`，只表示用户允许本次只读查看，不保存账号、Cookie、令牌或凭据。验证码或账号挑战写入 `detail` 和 `limitations`，结果只能是 `blocked`，随后停止该会话路径。

## 停止条件

- 同一 URL、同一模式的暂时网络错误最多重试一次；相同风控结果不循环换接口。
- 看到验证码、滑块、短信验证、设备确认或账号异常提示立即停止，不尝试自动点击或规避。
- 任何路径要求发布内容、触发关注/收藏/评价、改变地址、领取优惠或进入结算时立即停止。
- 页面可读范围已经全部完成后停止继续翻页；不要为了“更多数据”进入与候选无关的商品或账号页面。
