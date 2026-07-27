# 启动 / 重启 雨桐

雨桐 = **两个独立进程**，各占一个端口：

| 进程 | 端口 | 启动 | 改了代码后 |
|---|---|---|---|
| 后端 server（Python 语音管线） | 8765 | `scripts\run_server.ps1` | **必须重启**（Python 不热更新） |
| 前端 web（Next.js 界面） | 3000 | `scripts\run_web.ps1` | 自动热更新，刷新浏览器即可 |

> 浏览器用 `http://localhost:3000`。麦克风只在 localhost / HTTPS 下可用。

---

## 1. 第一次启动（或全部重启）

两个终端各开一个：

```powershell
# 终端 1 —— 后端（等到 "ready on ws://127.0.0.1:8765/ws" 再开下一个）
powershell -ExecutionPolicy Bypass -File scripts\run_server.ps1

# 终端 2 —— 前端
powershell -ExecutionPolicy Bypass -File scripts\run_web.ps1
```

然后浏览器打开 http://localhost:3000 。

---

## 2. 改了后端代码（`server/` 下的 .py）后，怎么跑最新版

Python 进程**不会**自动加载新代码——旧进程一直占着 8765 端口跑旧代码。所以必须先停旧的，再启新的：

```powershell
# 停掉占着端口的旧进程（8765 + 3000 都清）
powershell -ExecutionPolicy Bypass -File scripts\stop.ps1

# 重新启动后端（加载最新代码）
powershell -ExecutionPolicy Bypass -File scripts\run_server.ps1
```

> 如果你能找到原来跑 server 的那个终端，在里面按 `Ctrl+C` 也行——`stop.ps1` 是给"终端找不到了 / 关掉了"的情况兜底用的。

开发时嫌每次手动重启烦，可以加 `-Reload`，uvicorn 会监视 .py 文件、保存即自动重启后端（代价：每次重载要重新加载 Whisper+TTS，约 10 秒）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_server.ps1 -Reload
```

## 3. 改了前端代码（`web/` 下的 .tsx）后

**不用重启**。Next.js dev server 自带热更新，保存后刷新浏览器即可。只有 `run_web.ps1` 没在跑时才需要启动它。

---

## 常见报错

- **`[Errno 10048] ... bind on address ('127.0.0.1', 8765)`** → 旧后端还在跑。跑 `scripts\stop.ps1` 再重启。
- **`Error: listen EADDRINUSE :::3000`** → 旧前端还在跑。跑 `scripts\stop.ps1` 再重启。
- 这两个本质是同一个问题：关掉终端 ≠ 关掉进程。养成"重启前先 `stop.ps1`"的习惯就不会再撞。
