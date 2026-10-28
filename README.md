# 🐟 大肥鱼 · 抖音 AI 虚拟主播

一套能在抖音开播的 AI 虚拟主播程序：观众发弹幕，大肥鱼用自己的声音实时回答，嘴型跟着声音动，台词显示成字幕。观众还可以点歌，没人说话时会自动放背景音乐。

**第一次使用请看：[📖 图文教程](docs/图文教程.md)**，从安装 Python 到在直播伴侣里开播，每一步都有截图。这份文档也可以**直接交给 AI 助手**（Claude Code、Codex 等），让它帮你把环境配好。

简明版说明见 [docs/大肥鱼定制版使用说明.md](docs/大肥鱼定制版使用说明.md)；配置好以后，可以用 `.venv\Scripts\python tools\check_setup.py --online` 做开播前自检。

## 能做什么

| 功能 | 说明 |
|---|---|
| 弹幕实时回复 | 接收抖音直播间弹幕，交给大模型回答。默认配的是智谱的免费模型 `glm-4.7-flash` |
| 大肥鱼的声音 | 用 Fish Audio 合成语音，默认音色是「蓝色大肥鱼」；不想注册的话可以换成免费的 edge-tts |
| 海底直播间 | 横屏 1920×1080、竖屏 1080×1920 两套场景，背景前面是能动的 Live2D 角色 |
| 字幕与口型 | 台词逐字显示，说完自动隐藏；嘴型跟着实际播放的声音动 |
| 点歌 | 观众发「点歌 歌名」；本地 `song/` 文件夹里没有的歌，会自动去网易云搜索下载，同时显示歌名和歌词 |
| 防止点歌堵弹幕 | 有人在排队时，每首歌最多放 30 秒，切走的歌排在后面接着放 |
| 空闲背景乐 | 没人说话时自动放音乐，她一开口就让开 |
| 礼物与进场 | 收到小心心会念昵称送祝福；观众进场时现场生成一句欢迎语 |
| 异常推送 | 程序出错时可以推送到钉钉、Telegram 等，默认关闭 |

## 运行流程

```text
抖音直播间弹幕 → 弹幕监听程序 (8888) → 主程序 → 大模型回答 → Fish Audio 配音 → 电脑播放
                                                              ↓
                       OBS 浏览器来源：海底场景 + Live2D + 字幕 + 口型 → 虚拟摄像头 → 抖音直播伴侣
```

## 快速开始

```bat
git clone https://github.com/zbsdsb/dafeiyu-ai-vtuber.git
cd dafeiyu-ai-vtuber
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements\core+platform.lock.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

装好后还需要准备三样东西，下载地址和放置位置都写在[使用说明](docs/大肥鱼定制版使用说明.md)里：

1. **大肥鱼 Live2D 模型**：放到 `Live2D/live2d-model/大肥鱼/`。
2. **抖音弹幕监听程序**：放到 `tools/DouyinBarrageGrab/`。
3. **两个 Key**：智谱大模型的 Key，以及 Fish Audio 的 Key。

然后双击 `启动WebUI管理后台.bat`，在浏览器打开 http://127.0.0.1:8081 填好 Key，点右下角「操作 → 一键运行」。

## 仓库里不含的东西

API Key、大肥鱼模型、弹幕监听程序、歌曲文件都不在仓库里，需要你自己准备。`config.json` 里所有 Key 都是空的，填你自己的就行。

## 来源与许可

- 本项目基于 [Ikaros-521/AI-Vtuber（Luna AI）](https://github.com/Ikaros-521/AI-Vtuber) 二次开发。上游的完整功能、各平台和模型的对接说明请到原仓库查看。上游作者声明：个人使用免费，商用需联系作者授权。
- 代码沿用 GPL-3.0 许可，详见 [LICENSE](LICENSE)。
- 大肥鱼 Live2D 模型来自 [xkk6663/dsh-live2d-mao](https://github.com/xkk6663/dsh-live2d-mao)，模型作者 @Mao绒绒家园，**非商业使用，禁止盗用与出售**。
- 抖音弹幕监听使用 [dy-barrage-grab](https://gitee.com/haodong108/dy-barrage-grab)，遵循其自身的免责声明。
- 使用 AI 虚拟主播直播时，请遵守平台规则：明确标识 AI 生成内容，开播期间有真人值守，可以随时接管和互动。
