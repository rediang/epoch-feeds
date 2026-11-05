# epoch-feeds

纪元自建 RSS：GitHub Actions 每小时抓取上游源，清洗后发布到 GitHub Pages。

也镜像部分 YouTube 频道官方 Atom 源（`youtube.json`），写入 `feeds/yt-<channel_id>.xml`，保留原始 `yt:videoId` / `media:*` 字段；抓取失败时保留上次成功的文件。

## 用法

启用 Pages（Settings → Pages → Source: GitHub Actions）后，地址形如：

`https://rediang.github.io/epoch-feeds/feeds/zaobao.xml`

YouTube 镜像示例：

`https://rediang.github.io/epoch-feeds/feeds/yt-UCRtmhO2ahi0rDcRadfeNtFQ.xml`

把该 URL 填进 eponews「纪元RSS」订阅源。

手动触发：Actions → Build RSS feeds → Run workflow。

## 费用

0（GitHub 免费额度内）。
