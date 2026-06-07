# Edge 收藏导航

一个基于 FastAPI、SQLite 和原生前端的私有收藏导航系统，支持 Edge 导入与同步、手动编辑、网页抓取、AI 分类、多模型路由、语义搜索和本地文件副本。

## 启动

1. 复制 `.env.example` 为 `.env`。
2. 生成加密密钥：

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

3. 设置 `SECRET_KEY`、`ENCRYPTION_KEY` 和首次启动密码。
4. 启动：

   ```powershell
   docker compose up -d --build
   ```

5. 打开 `http://127.0.0.1:8765/login`。

数据库和 API 时间以 UTC 保存，界面与容器日志统一显示为 `Asia/Shanghai`。

## AI 模型

后台“AI 模型”支持：

- 配置多个供应商，每个供应商独立保存 Base URL 和加密 API Key。
- 在供应商下配置多个聊天、分类、摘要或 Embedding 模型。
- 单独指定默认生成、分类、摘要和搜索模型。
- 测试供应商和具体模型的连通性。

旧版 AI 设置会在首次打开模型管理页时自动迁移为“默认供应商”。

## 持久化搜索

系统同时使用 SQLite FTS5 和 Embedding：

- 收藏向量保存在 SQLite，不会在每次搜索时重建。
- 新增或修改收藏只更新对应记录。
- 相同查询向量缓存 30 天，AI 搜索回答按模型与索引版本持久复用。
- 切换 Embedding 模型或修改当前模型名称时，才会创建全量后台重建任务。
- 重建期间继续使用上一套完整索引。
- Embedding 不可用时自动退回关键词搜索。
- 普通搜索直接使用浏览器中的导航快照，智能搜索和 AI 搜索按需访问服务端。

首次升级会自动为已有收藏建立全文索引和图片缓存。

## Edge HTML 导入

在后台“导入与同步”上传 Edge 导出的 HTML。导入器会保留完整文件夹路径，并按规范化 URL 合并重复收藏。

旧 JSON 迁移：

```powershell
$env:PYTHONPATH="backend"
python scripts/migrate_legacy.py bookmarks_final.json
```

## 本地定时同步

1. 在后台生成带 `sync` 权限的 Token。
2. 复制 `client/sync_config.example.json` 为 `client/sync_config.json`。
3. 填写服务器地址、Token 和 Edge Profile。
4. 手动验证：

   ```powershell
   python client/edge_sync.py
   ```

5. 安装每日任务：

   ```powershell
   powershell -ExecutionPolicy Bypass -File client/install_task.ps1 -Time "03:00"
   ```

快照未变化时客户端不会请求服务器。`file://` 收藏可按允许的扩展名和大小限制上传为认证后的服务器副本。

## 账号和安全

- 管理员密码使用 Argon2 哈希保存。
- 后台可以修改用户名和密码，保存后撤销所有现有会话。
- `ADMIN_PASSWORD` 仅在数据库中不存在管理员时用于首次初始化；创建完成后可从 `.env` 删除。
- 浏览器写操作使用 CSRF Token，远程客户端使用单独的 Bearer Token。
- AI Key 加密保存，页面只显示配置状态。
- 抓取阻止内网、回环和保留 IP，并逐次验证重定向。
- 删除采用软删除。

## 升级与乱码检查

升级前备份数据库，然后执行：

```powershell
cd backend
alembic -c alembic.ini upgrade head
```

乱码修复先预览：

```powershell
$env:PYTHONPATH="backend"
python scripts/repair_mojibake.py
```

确认备份后才执行：

```powershell
python scripts/repair_mojibake.py --apply
```

## 测试

```powershell
python -m pytest -q
```
