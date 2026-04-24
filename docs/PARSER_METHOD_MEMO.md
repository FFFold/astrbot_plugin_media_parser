# 分平台解析方法说明

本文仅说明各平台解析器如何从输入链接取得标准媒体元数据，不覆盖下载、缓存、中转和消息发送流程。

标准解析输出主要包含：

- `url` / `source_url`
- `title` / `author` / `desc` / `timestamp`
- `video_urls` / `image_urls`
- `video_headers` / `image_headers`
- 平台特定的访问状态、代理标记或预下载标记

---

## Bilibili

适用链接：

- `b23.tv/...`
- `www.bilibili.com/video/av...`
- `www.bilibili.com/video/BV...`
- `www.bilibili.com/bangumi/...`
- `www.bilibili.com/opus/...`
- `t.bilibili.com/...`

解析思路：

Bilibili 解析器先统一展开短链并识别内容类型，再分别走 UGC 视频、PGC 番剧或动态解析路径。视频解析优先使用官方接口获取结构化数据和播放地址；动态解析从动态结构中提取文本、图片和视频卡片。

具体步骤：

1. 展开 `b23.tv` 等短链，得到最终页面链接。
2. 读取可用 Cookie，用于高画质、会员画质和访问受限内容判断。
3. 判断是否为直播链接；直播链接直接跳过。
4. 若为 `opus` 或 `t.bilibili.com`，进入动态解析路径。
5. 若为普通视频，提取 `bvid` 或 `aid`，获取视频详情、分 P 列表和当前分 P 的 `cid`。
6. 若为番剧，提取 `ep_id` 或 `season_id`，获取番剧详情和播放信息。
7. 调用播放接口，优先构造 DASH 音视频组合地址；无法构造 DASH 时使用普通播放直链。
8. 分析访问状态，补充是否完整可看、是否仅预览、限制类型和提示文案。
9. 整理标题、作者、简介、发布时间、媒体 URL、请求头和热评。

---

## Douyin

适用链接：

- `v.douyin.com/...`
- `www.douyin.com/video/...`
- `www.douyin.com/note/...`

解析思路：

抖音解析器先展开分享短链，再根据最终链接判断视频或图文笔记。实际数据从 `iesdouyin.com/share/...` 页面内的 `window._ROUTER_DATA` 中提取。

具体步骤：

1. 使用 `HEAD` 请求跟随重定向，得到最终链接。
2. 判断是否为直播链接；直播链接直接跳过。
3. 根据路径判断内容类型：`/note/` 为图文笔记，`/video/` 为视频。
4. 提取视频或笔记 ID。
5. 请求 `https://www.iesdouyin.com/share/video/{id}/` 或 `https://www.iesdouyin.com/share/note/{id}/`。
6. 从页面中提取 `window._ROUTER_DATA` 并解析 JSON。
7. 在 `loaderData` 中查找 `videoInfoRes` 或 `noteDetailRes`。
8. 从 `item_list[0]` 提取标题、作者、发布时间。
9. 若存在 `images`，按图文笔记返回图片列表；否则从 `video.play_addr.uri` 构造或读取视频地址。
10. 补充移动端 UA、Referer 和标准元数据字段。

---

## Kuaishou

适用链接：

- `v.kuaishou.com/...`
- `www.kuaishou.com/short-video/...`
- `m.gifshow.com/fw/photo/...`

解析思路：

快手解析器以网页 SSR 数据为主，优先解析 `INIT_STATE` 中的 `photo` / `single` 数据；当结构化数据不可用时，回退到旧版正则和 `rawData` 解析。

具体步骤：

1. 请求页面 HTML，必要时将稀疏分享页转换为 `m.gifshow.com` 详情页。
2. 从 HTML 中提取作者、标题等基础元数据。
3. 优先解析 `window.__APOLLO_STATE__` / `INIT_STATE`。
4. 在 `INIT_STATE` 中查找包含 `photo` 的节点，并解析 `photo` 和 `single`。
5. 若 `photo.mainMvUrls` 存在，提取视频 CDN URL。
6. 若为图集，读取 `single.cdnList`、图片路径和背景音乐信息，构造图片 URL 列表。
7. 若 SSR 解析失败，使用正则从 HTML 中提取 `.mp4` 视频 URL。
8. 若仍失败，解析 `window.rawData`，从旧结构中提取视频或图集图片。
9. 根据 `photo.timestamp` 或媒体 URL 推断发布时间。
10. 返回视频或图集元数据与下载请求头。

---

## Weibo

适用链接：

- `weibo.com/...`
- `m.weibo.cn/detail/...`
- `video.weibo.com/show?...`
- `weibo.com/tv/show/...`

解析思路：

微博解析器先按 URL 类型分流，再获取访客 Cookie。桌面端微博走 `ajax/statuses/show`，移动端微博解析页面内 `$render_data`，视频页走 `tv/api/component`。

具体步骤：

1. 判断链接类型：桌面微博、移动微博或微博视频页。
2. 请求微博访客接口或页面，获取解析所需 Cookie。
3. 桌面微博：提取微博 ID，调用 `https://weibo.com/ajax/statuses/show`。
4. 移动微博：请求 `https://m.weibo.cn/detail/{id}`，解析页面内 `var $render_data`。
5. 视频微博：提取视频 ID，向 `https://weibo.com/tv/api/component` 发送表单请求。
6. 从返回数据中提取正文、作者、发布时间和媒体字段。
7. 将媒体 URL 拆分为视频列表和图片列表。
8. 补充微博需要的 Referer、Cookie、视频请求头和图片请求头。
9. 如配置启用热评，追加热门评论数据。

---

## Xiaohongshu

适用链接：

- `xhslink.com/...`
- `www.xiaohongshu.com/explore/...`
- `www.xiaohongshu.com/discovery/item/...`

解析思路：

小红书解析器通过页面内 `window.__INITIAL_STATE__` 获取笔记数据，同时兼容移动端和 PC 端两种状态树结构。根据笔记类型返回视频或图片列表。

具体步骤：

1. 若为 `xhslink.com`，先展开到完整笔记链接。
2. 清理分享参数，例如 `source`、`xhsshare`。
3. 判断是否为直播链接；直播链接直接跳过。
4. 根据 PC/移动端链接选择对应请求头并拉取 HTML。
5. 从 HTML 中提取 `window.__INITIAL_STATE__`。
6. 优先读取移动端路径 `noteData.data.noteData`。
7. 若移动端路径不可用，读取 PC 端路径 `note.noteDetailMap[noteId].note`。
8. 提取标题、正文、作者、发布时间和笔记类型。
9. 视频笔记从 `video.media.stream.h264[0].masterUrl` 读取视频地址。
10. 图文笔记从 `imageList` 中读取图片地址。
11. 如配置启用热评，从状态树中收集评论数据。

---

## Xiaoheihe

适用链接：

- `www.xiaoheihe.cn/app/topic/game/...`
- `api.xiaoheihe.cn/game/share_game_detail?...`
- `www.xiaoheihe.cn/app/bbs/link/...`
- `api.xiaoheihe.cn/v3/bbs/app/api/web/share?...`

解析思路：

小黑盒解析器先识别游戏详情页或 BBS 帖子页。游戏详情页优先走带签名的 Web API，失败后回退 Nuxt 页面数据；BBS 帖子页走签名接口 `/bbs/app/link/tree`。

具体步骤：

1. 识别 URL 类型：游戏详情页或 BBS 帖子页。
2. 游戏详情页提取 `appid` 和 `game_type`，`appid` 保留为字符串以兼容字母数字混合分享 ID。
3. 生成小黑盒 Web API 签名参数 `hkey`、`_time`、`nonce`。
4. 生成或复用设备 token，并作为 `x_xhh_tokenid` Cookie。
5. 游戏详情页优先请求 `/game/get_game_detail/`。
6. 从游戏详情 API 结果中提取标题、评分、简介、厂商、价格、奖项、统计信息、视频和图片。
7. 如详情 API 失败，拉取 Web 页面 HTML。
8. 从 HTML 中提取 `__NUXT_DATA__`，还原 Nuxt devalue 数据结构。
9. 在还原后的对象树中定位游戏详情对象，并补齐媒体和展示字段。
10. BBS 帖子页提取 `link_id`，请求 `/bbs/app/link/tree`。
11. 从 BBS 返回结构中解析视频、图文正文、图片、标签和互动信息。

---

## Twitter / X

适用链接：

- `twitter.com/.../status/...`
- `x.com/.../status/...`

解析思路：

Twitter/X 解析器优先使用 FxTwitter 获取简化媒体结构；当 FxTwitter 不可用时，回退 Twitter Guest GraphQL。视频结果会标记为倾向预下载，以提升发送稳定性。

具体步骤：

1. 从链接中提取 Tweet ID。
2. 请求 `https://api.fxtwitter.com/status/{tweet_id}`。
3. 从 FxTwitter 响应中提取正文、作者、发布时间、图片和视频。
4. 若 FxTwitter 失败，调用 Twitter Guest Token 接口获取 `guest_token`。
5. 使用 Bearer Token、Guest Token 和 GraphQL 参数请求 `TweetResultByRestId`。
6. 从 GraphQL 返回的 `legacy.extended_entities.media` 或 `legacy.entities.media` 中提取媒体。
7. 图片使用原图 URL。
8. 视频和动图从 `video_info.variants` 中选择最高码率 MP4。
9. 视频 URL 添加 Range 下载前缀，并标记 `force_pre_download=True`。
10. 返回正文、作者、时间、媒体 URL 和代理配置字段。
