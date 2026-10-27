# OpenCLI 京东评论读取器

仅在当前任务已获用户授权、且需要读取京东登录后正常可见的商品评价时使用。淘宝与天猫不使用本流程，直接走淘宝官方桌面客户端。它是只读辅助工具，不是验证码绕过器，也不替代独立投诉与体验来源。

## 命令

```powershell
opencli jd reviews <sku> --limit 100 --pages 20 --format json --trace off --keep-tab true
```

只读入口必须由 `SHOPPING_REVIEW_READER` 指向经过审核的 `Read-Reviews.ps1`，或由用户显式提供路径。未配置时停止并报告依赖缺失；不要猜测本机路径，也不要改用购物车、收藏、下单或其他写命令。

## 可信成功条件

普通 JSON 数组不自动代表成功。把输出写入 `review_coverage` 前同时确认：

- 每行 `filter` 为 `negative`，`errorCode` 为 `null`，`adapterVersion` 非空；
- `pagesRead` 是正整数，且各行值一致；
- `exhausted=true` 才表示已读完适配器可访问的分页范围；若为 `false`，结果只能记 `partial`；
- 返回条数与逐条 `negative_reviews` 记录数一致；追评、规格、星级和商家回复按实际可见值记录，缺失用 `null`，不得推测；
- 平台显示的差评总量大于返回量时，即使 `exhausted=true` 也按矛盾处理并记 `BLOCKED`。

平台显示“99%–100% 好评率”但没有可验证的差评筛选、负面正文或分页时，不得把好评率当成零差评，也不得单凭好评率直接淘汰商品。记录为评价展示/操纵风险信号；`review_completeness` 因正文缺失可为 `BLOCKED`，`manipulation` 则结合其他操纵证据评估。

适配器只有在差评筛选明确显示 0、已访问至少一页且没有下一页时，才抛出可采信的 `EMPTY_RESULT`，不会返回 `[]`。京东页面在不同标签租约中可能短暂显示“累计评价 0”或空评价区，即使同一 SKU 的正常页面显示 `100+` 或 `200+`；因此没有渲染差评筛选时，即使同时出现“暂无评价”，也必须按 `COMMAND_EXEC` 处理，不能记录为零评论。

本机 `Read-Reviews.ps1` 在运行京东适配器前，会先用 `jd-login` 持久商品页核对实际登录状态并打开正常可见的“全部评价”。若该页面明确显示“页面正在维修中，请稍后重试”，应直接记录 `PLATFORM_COMPONENT_UNAVAILABLE / BLOCKED`；适配器临时页随后出现的“累计评价 0 / 暂无评价”不能覆盖这项更强的现场证据。

只有错误提示明确写明差评筛选为零、适配器版本、`pagesRead` 和 `exhausted=true`，才可记录当前可见范围为零条。只有空数组、缺少筛选、页面空状态或总评论数字为零都不能单独证明零差评；零条也不证明商品没有问题。

## 错误映射

| OpenCLI 结果 | `access_result` | `error_code` | 处理 |
|---|---|---|---|
| 有评论行且 `exhausted=true` | `complete` | `null` | 逐条写入 `negative_reviews` |
| 有评论行但 `exhausted=false` | `partial` | `PAGINATION_INCOMPLETE` | 保留已读内容，门控为 `BLOCKED` |
| `EMPTY_RESULT` 且明确差评筛选为 0 | `complete` | `null` | `negative_reviews=[]`，保留筛选和分页证据 |
| 其他 `AUTH_REQUIRED` | `blocked` | `AUTH_REQUIRED` | 人工登录或验证后可重试一次；不得自动处理挑战 |
| `COMMAND_EXEC` 且京东完整评价组件明确显示“页面正在维修中，请稍后重试” | `blocked` | `PLATFORM_COMPONENT_UNAVAILABLE` | 保留商品页预览证据；最多重试一次，不得把预览或空壳当作完整评论 |
| `COMMAND_EXEC` | `blocked` | `PAGE_CHANGED_OR_UNRECOGNIZED` | 视为页面改版或提取失败，不得写零条 |
| `TIMEOUT` | `blocked` | `TIMEOUT` | 同一路径最多重试一次 |
| 任何未知错误 | `blocked` | `UNKNOWN_READER_ERROR` | 保留原错误摘要，不作质量结论 |

出现登录页、滑块、验证码、短信验证、设备确认或风控页时立即停止。用户说“已经登录”也不能覆盖本次命令看到的京东专用会话证据；应报告该会话的实际状态，不读取默认浏览器 Cookie，也不要求用户提供凭据。

京东使用命名持久会话 `jd-login`。失败命令释放临时标签后短暂出现 `about:blank`，只表示临时页被关闭，不是登录失效证据。读取失败后应让命名会话停留在当前公开商品页；若页面自动跳到账户中心、订单、地址、消息或支付页面，立即返回公开商品页，不读取无关账户内容。

商品页仅显示少量正面评价预览、却没有可验证的差评筛选和分页时，这些预览只能记录为线索，不能写入 `negative_reviews`，评论完整性仍为 `BLOCKED`。

## 记录映射

新记录使用 `schema_version: 3`。`review_coverage` 至少增加：

```json
{
  "negative_reviews": [
    {
      "source_url": "https://item.example/123",
      "date": "2026-10-19",
      "variant": "450mL",
      "stars": 1,
      "summary": "杯盖有持续异味",
      "follow_up": "数日后仍有味道",
      "seller_response": null
    }
  ],
  "reader_evidence": {
    "backend": "opencli",
    "command": "jd reviews 123",
    "captured_at": "2026-10-19T10:00:00+08:00",
    "source_url": "https://item.example/123"
  },
  "filter_verified": true,
  "pages_read": 1,
  "exhausted": true,
  "adapter_version": "safe-review-reader/1.1.0",
  "error_code": null
}
```

京东适配器当前报告 `safe-review-reader/1.0.4`。只要求版本非空并与本次实际输出一致，不得手工改写。

不要保存用户名、头像、账号标识、Cookie、令牌、完整地址、订单信息或页面中的其他无关个人资料。
