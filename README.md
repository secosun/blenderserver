# BlenderServer — CADRender SaaS Middle-tier

SaaS 中台，位于前端与 blenderworker 之间：

```
用户选择模板/调参 → POST /api/tasks → 消息队列 → blenderworker → Blender 渲染 → 回调进度
```

## 技术栈

| 层 | 技术 |
|---|------|
| 框架 | FastAPI + uvicorn |
| 数据库 | PostgreSQL / SQLite（开发），SQLAlchemy 2.0 async |
| 消息队列 | Redis / In-Memory（开发）|
| 对象存储 | MinIO（S3 兼容）/ 本地文件系统 |
| AI | Claude API（文字→渲染参数，可选）|
| Worker | Blender headless Cycles（TCP :19876）|
| 认证 | JWT + API Key |
| 支付 | Stripe + 支付宝/微信 |

## 快速启动

Docker Compose（推荐）：`docker compose -f ../docker-compose.dev.yml --profile full up -d`

直接运行：
```powershell
pip install -r requirements.txt
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8060
```

## 关键 API

| 路径 | 说明 |
|------|------|
| `POST /api/auth/register` | 注册 |
| `POST /api/auth/login` | 登录 |
| `GET /api/finishes` | 材质列表 |
| `POST /api/tasks` | 创建渲染任务 |
| `GET /api/tasks` | 任务列表 |
| `WS /api/ws/{id}` | WebSocket 进度 |
| `GET /api/admin/calibration-reports/{finish_id}` | 材质校准报告 |
| `GET /api/scenes-engine` | 场景引擎列表 |
| `GET /api/category-finishes` | 分类→材质映射 |
| `POST /api/stripe-webhook` | 支付回调 |

完整 API 文档：`http://localhost:8060/docs`
