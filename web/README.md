# we-mp-mini Web Console

Vue + Vite 前端控制台，对接后端 `we-mp-mini` API。

## 启动

```bash
npm install
npm run dev
```

默认访问：`http://127.0.0.1:5173`

## 环境变量

复制并调整：

```bash
cp .env.example .env
```

- `VITE_API_BASE`：API 前缀，默认 `/api/v1`
- `VITE_DEV_API_TARGET`：Vite 代理目标，默认 `http://127.0.0.1:18011`

## 打包

```bash
npm run build
```

产物目录：`web/dist`
