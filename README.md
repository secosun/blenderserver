# BlenderServer — CADRender SaaS Middle-tier

SaaS 中台服务，位于 **Web 前端** 与 **blenderworker（渲染执行内核）** 之间：

```
用户上传 3D 模型（FCStd/OBJ/STL/STEP/FBX/GLB）
       │
       ▼
  选择场景预设 ──→ 直接映射渲染参数
  或文字描述 ─────→ LLM 转换为渲染参数（Claude API）
       │
       ▼
  POST /api/tasks ──→ 消息队列 ──→ Worker ──→ Blender (Cycles/EEVEE)
                                        │
                                        └── 回调进度 → WebSocket → 前端
```

## 技术栈

| 层 | 技术 |
|---|------|
| 框架 | FastAPI + uvicorn |
| 数据库 | PostgreSQL 16（生产）/ SQLite（开发），SQLAlchemy 2.0 async |
| 消息队列 | Redis 7（生产）/ In-Memory（开发）|
| 对象存储 | MinIO（S3 兼容）/ 本地文件系统 |
| AI 推理 | Claude API（文字 → 渲染参数） |
| Worker | Blender 3.6+ headless（Cycles/EEVEE） |
| 认证 | JWT Bearer + X-API-Key 双方案 |
| 可观测性 | Prometheus 指标 + JSON 结构化日志 |

## 启动方式

### 方式一：Windows 开发（推荐）

基础设施（PostgreSQL + Redis + MinIO）运行在 **WSL 2 Docker** 内，应用服务运行在 **Windows 本机**。

```powershell
# ── Step 1: WSL 内启动 Docker 和基础设施 ──
wsl -d Ubuntu-22.04 -u root
# 在 WSL shell 内执行：
nohup dockerd > /var/log/dockerd.log 2>&1 &
sleep 2
cd /mnt/d/咸阳/框架评审/CADRender/blenderserver
docker compose up -d postgres redis minio
exit

# ── Step 2: Windows 本机启动 blenderserver ──
cd D:\咸阳\框架评审\CADRender\blenderserver

$env:DATABASE_URL="postgresql+asyncpg://cadrender:cadrender@localhost:5432/cadrender"
$env:QUEUE_BACKEND="redis"
$env:REDIS_URL="redis://localhost:6379/0"
$env:STORAGE_BACKEND="s3"
$env:S3_ENDPOINT="http://localhost:9000"
$env:S3_ACCESS_KEY="minioadmin"
$env:S3_SECRET_KEY="minioadmin"
$env:S3_BUCKET="cadrender"
$env:JWT_SECRET="dev-jwt-secret"
$env:WORKER_CALLBACK_SECRET="dev-callback-secret"

python -m uvicorn main:app --reload --host 0.0.0.0 --port 8888
```

验证：`curl http://localhost:8888/health`

### 方式二：全 Docker（生产部署）

```bash
docker compose up -d
# → http://localhost:8060
```

### 方式三：纯本地快速开发（跳过 PG/Redis/MinIO）

```powershell
cd D:\咸阳\框架评审\CADRender\blenderserver
pip install -r requirements.txt
# 使用 SQLite + 内存队列 + 本地存储
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8888
```

此模式用 `core/config.py` 的默认值（SQLite + InMemoryQueue + LocalStorage）。

## 架构全景

```
┌──────────────────────────────────────────────────────────────────┐
│                          Windows 本机                             │
│  ┌────────────────────────────────────┐  ┌─────────────────────┐  │
│  │  uvicorn (main:app) port:8888      │  │  Blender (headless)  │  │
│  │  ─── FastAPI + WebSocket            │  │  blenderworker       │  │
│  │  ─── TaskManager                    │  │  socket:19876        │  │
│  │  ─── Prometheus /metrics            │  └─────────────────────┘  │
│  └──────┬──────────┬──────────┬───────┘                           │
│         │          │          │                                    │
└─────────┼──────────┼──────────┼────────────────────────────────────┘
          │          │          │
     WSL  │     WSL  │     WSL  │  (localhost 自动转发)
          │          │          │
┌─────────▼──────────▼──────────▼────────────────────────────────────┐
│                      WSL 2 Docker                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                          │
│  │PostgreSQL│  │  Redis   │  │  MinIO   │  S3-compatible storage   │
│  │:5432     │  │:6379     │  │:9000     │                          │
│  │          │  │          │  │:9001     │  Web console              │
│  └──────────┘  └──────────┘  └──────────┘                          │
└────────────────────────────────────────────────────────────────────┘
```

## API 端点

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/auth/register` | 注册（邮箱+密码） |
| `POST` | `/api/auth/login` | 登录，返回 JWT |
| `GET` | `/api/auth/me` | 当前用户信息（需认证） |
| `POST` | `/api/auth/api-keys` | 创建 API Key（需认证） |
| `GET` | `/api/auth/api-keys` | API Key 列表（需认证） |
| `DELETE` | `/api/auth/api-keys/{id}` | 吊销 API Key（需认证） |

### 模型上传

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/upload` | 上传 3D 模型（FCStd/OBJ/STL/STEP/FBX/GLB/BLEND） |

### 场景预设

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/scenes` | 所有场景预设列表 |

### 任务管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/tasks` | 创建渲染任务 |
| `GET` | `/api/tasks` | 任务列表 |
| `GET` | `/api/tasks/{id}` | 任务详情 |
| `POST` | `/api/tasks/{id}/dispatch` | 投递到 Worker |
| `POST` | `/api/tasks/{id}/cancel` | 取消任务 |
| `GET` | `/api/tasks/{id}/result` | 获取渲染结果 |

### 实时推送

| 方法 | 路径 | 说明 |
|------|------|------|
| `WS` | `/api/ws/{id}` | WebSocket 实时进度 |

### Worker 回调（内部）

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/worker/callback/{id}` | Worker 上报进度（需密钥鉴权） |

### Worker 池管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/workers/register` | Worker 注册 |
| `POST` | `/api/workers/{id}/heartbeat` | Worker 心跳（每 30s） |
| `GET` | `/api/workers` | 所有 Worker 列表（管理端） |
| `GET` | `/api/workers/capacity` | 当前可用容量 |

### 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/admin/users` | 用户列表（admin） |
| `PATCH` | `/api/admin/users/{id}` | 修改用户（admin） |
| `PATCH` | `/api/admin/users/{id}/quota` | 修改配额（admin） |
| `GET` | `/api/admin/audit-log` | 审计日志（admin） |
| `GET` | `/api/admin/dead-letter` | 死信队列（admin） |
| `POST` | `/api/admin/dead-letter/replay` | 重放死信（admin） |
| `GET` | `/api/admin/status` | 系统状态（admin） |

### 可观测性

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/metrics` | Prometheus 指标 |
| `GET` | `/health` | 健康检查 |

## 任务状态机

```
pending → ready → queued → running → completed
                              ↓
                            failed
              任意状态 → cancelled
```

- **pending**: 已创建，等待构建渲染参数
- **ready**: 渲染参数已就绪（来自场景预设或 LLM）
- **queued**: 已投递到消息队列，等待 Worker
- **running**: Worker 正在渲染（WebSocket 推送实时进度）
- **completed**: 渲染完成，`result_url` 可下载
- **failed**: 渲染失败，`error_message` 说明原因

## 场景预设（11 个）

| ID | 名称 | 适用场景 |
|----|------|---------|
| `studio_champagne` | 香槟金 工作室标准 | 铝型材、门窗框 |
| `studio_black_matte` | 哑光黑 工作室标准 | 黑色粉末涂层 |
| `studio_gunmetal` | 枪灰色 金属质感 | 栏杆、工业件 |
| `studio_automotive` | 汽车烤漆 高光泽 | 涂层表面件 |
| `studio_white_soft` | 柔光白 简洁风 | 白色/浅色产品 |
| `studio_orange` | 橙色粉末涂层 | 亮色系产品 |
| `detail_closeup` | 局部特写 | 表面处理工艺展示 |
| `transparent_black` | 黑底透明背景 | 电商主图后期 |
| `transparent_champagne` | 香槟金 透明背景 | 电商白底图 |
| `freecad_profile_preview` | 型材截面 快速预览 | FreeCAD 设计迭代 |

## 使用示例

### 1. 注册

```bash
curl -X POST http://localhost:8888/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"password123","display_name":"我的账号"}'
```

### 2. 上传模型

```bash
curl -X POST http://localhost:8888/api/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@/path/to/model.obj"
```

### 3. 创建渲染任务

```bash
# 选场景预设
curl -X POST http://localhost:8888/api/tasks \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"model_id":"<model_id>","scene_id":"studio_champagne"}'

# 或文字描述
curl -X POST http://localhost:8888/api/tasks \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"model_id":"<model_id>","prompt":"枪灰色金属质感，3/4视角"}'
```

### 4. 投递渲染

```bash
curl -X POST http://localhost:8888/api/tasks/<task_id>/dispatch \
  -H "Authorization: Bearer <token>"
```

### 5. WebSocket 实时看进度

```python
import asyncio, websockets
async def watch():
    async with websockets.connect("ws://localhost:8888/api/ws/<task_id>") as ws:
        async for msg in ws:
            print(msg)
asyncio.run(watch())
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| **数据库** | | |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/...` | PostgreSQL 连接串（生产必填） |
| `DB_PATH` | `./data/blenderserver.db` | SQLite 路径（仅开发） |
| **消息队列** | | |
| `QUEUE_BACKEND` | `memory` | `memory` \| `redis` |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接串 |
| **对象存储** | | |
| `STORAGE_BACKEND` | `local` | `local` \| `s3` |
| `S3_ENDPOINT` | — | MinIO/S3 地址（如 `http://localhost:9000`） |
| `S3_ACCESS_KEY` | — | S3 Access Key |
| `S3_SECRET_KEY` | — | S3 Secret Key |
| `S3_BUCKET` | `cadrender` | S3 存储桶 |
| **认证** | | |
| `JWT_SECRET` | `dev-jwt-secret-...` | JWT 签名密钥（生产必改） |
| `JWT_EXPIRY_HOURS` | `72` | Token 有效期 |
| `WORKER_CALLBACK_SECRET` | `dev-secret` | Worker 回调鉴权（生产必改） |
| **AI** | | |
| `ANTHROPIC_API_KEY` | — | Claude API 密钥（选填，仅文字描述时需要） |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Claude 模型 |
| **服务器** | | |
| `SERVER_HOST` | `0.0.0.0` | 监听地址 |
| `SERVER_PORT` | `8060` | 端口 |
| `CORS_ORIGINS` | `*` | 允许的跨域来源 |
| `RATE_LIMIT_PER_MINUTE` | `60` | 每分钟每用户最大请求数 |

## 配置参考

完整配置项见 `core/config.py`。
