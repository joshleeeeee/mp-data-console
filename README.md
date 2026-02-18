# we-mp-mini

一个极简版微信公众号提取工具，只保留核心能力：

- 扫码登录微信公众号后台
- 抓取公众号文章（列表 + 正文）
- 导出 Markdown / HTML / PDF
- 提供 Vue + Vite 前端控制台
- 内置微信图片代理与导出图片本地化（减少防盗链导致的丢图）
- 提供低心智负担的三视图：抓取流程 / 数据库 / MCP 配置
- 支持抓取前确认公众号（含头像）与抓取条数，并支持正文预览

不包含 RSS 和复杂调度。

## 1. 环境要求

- Python 3.11+
- Linux / macOS / Windows

如果需要导出 PDF：

- 安装 Playwright 浏览器：`playwright install chromium`

## 2. 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 3. 启动

### 3.0 一键启动（推荐）

```bash
./scripts/dev-up.sh
```

首次如果你希望脚本顺便安装依赖：

```bash
./scripts/dev-up.sh --install
```

如果需要安装 PDF 导出依赖浏览器：

```bash
./scripts/dev-up.sh --install --install-playwright
```

停止服务：

```bash
./scripts/dev-down.sh
```

### 3.1 启动后端 API

```bash
uvicorn app.main:app --host 0.0.0.0 --port 18011 --reload
```

打开文档：`http://127.0.0.1:18011/docs`

### 3.2 启动前端控制台

```bash
cd web
npm install
cp .env.example .env
npm run dev
```

默认地址：`http://127.0.0.1:5173`

## 4. 使用流程（最小闭环）

1. 获取二维码
   - `GET /api/v1/auth/qr`
2. 打开二维码图片扫码
   - `GET /api/v1/auth/qr/image`
3. 轮询登录状态
   - `GET /api/v1/auth/status`
4. 搜索公众号
   - `GET /api/v1/mps/search?keyword=关键词`
5. 保存公众号到本地
   - `POST /api/v1/mps`
6. 提交后台抓取任务（推荐，可离开页面）
   - `POST /api/v1/mps/{mp_id}/sync/jobs`
   - `GET /api/v1/mps/sync/jobs/{job_id}`
   - `GET /api/v1/mps/sync/jobs`
6.5 同步公众号文章（阻塞模式，兼容保留）
   - `POST /api/v1/mps/{mp_id}/sync`
7. 查看文章
   - `GET /api/v1/articles`
7.5 图片代理（防盗链）
   - `GET /api/v1/assets/image?url=<原图地址>`
7.6 一键抓取（搜索 + 入库 + 同步）
   - `POST /api/v1/ops/quick-sync`
7.7 数据库在线浏览
   - `GET /api/v1/ops/db/tables`
   - `GET /api/v1/ops/db/table/{table_name}`
7.8 MCP 一键配置
   - `GET /api/v1/ops/mcp/config`
   - `POST /api/v1/ops/mcp/generate-file`
8. 导出单篇
   - `POST /api/v1/exports/article/{article_id}`
9. 批量导出 ZIP
   - `POST /api/v1/exports/batch`

## 5. 目录结构

```text
app/
  core/           # 配置 + DB
  routers/        # API 路由
  services/       # 微信认证/抓取/导出
  models.py       # SQLAlchemy 模型
  schemas.py      # Pydantic 模型
  main.py         # FastAPI 启动入口
web/
  src/            # Vue 页面与 API 调用
  vite.config.js  # 开发代理配置
scripts/
  dev-up.sh       # 一键启动前后端
  dev-down.sh     # 停止前后端
```

## 6. 注意事项

- 扫码登录态会过期，过期后需要重新扫码。
- 请求过快会触发微信风控或限频。
- 仅应抓取你有权限访问的账号内容。
- 本项目是研究/自用工具，请遵守平台条款与当地法规。
