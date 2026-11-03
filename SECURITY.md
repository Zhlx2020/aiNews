# 安全说明

## 暴露面

| 端点 | 是否公网 | 保护 |
|---|---|---|
| 面板 `8091` | 是 | 只放页面与快照；挡掉 `.env`、`.git/`、状态文件、日志、脚本 |
| Miniflux `8085` | 是 | HTTP Basic，`/v1/*` 匿名 401；账号 `radar`（管理员）为 22 位随机口令 |
| ntfy `8088` | 是 | 匿名只读；发布需 Bearer token（`NTFY_TOKEN`） |
| LibreTranslate `5001` | 是 | 无鉴权（本身无敏感数据，注意被白蹭 CPU） |

两台的 `lab-boot.service` 均以 `User=user` 运行，带 `NoNewPrivileges=yes`、`PrivateTmp=yes`。

## 秘密放在哪

只放机器上的 `/data/home/airadar/.env`（权限 `600`），不入库。仓库里只有 `.env.example`。

包含：Miniflux 口令、AMD API key、ntfy token、GitHub 推送 token。

## 已知残留

1. **AMD key 在公开 git 历史里**（首个提交曾硬编码）。要彻底清掉需重写历史；省事的做法是去控制台重新生成 key，只填进 `.env`。
2. **GitHub token 是 OAuth 令牌，权限覆盖全部仓库**。建议换成只对 `aiNews` 有写权限的细粒度 PAT。
3. **旧 Miniflux 口令 `radar-pass-2026` 已作废**，但也留在 git 历史里。
4. LibreTranslate 无鉴权，可能被人白用；量大时可加反向代理限速。
5. 面板无 HTTPS 自定义域，靠平台子域；如需更强可挂 Cloudflare 代理。

## 轮换步骤

改 `.env` 对应行 → `pkill -f airadar.py`，等 `loop.sh` 下一轮自动拉起（或手动重启 `loop.sh`）。

改 Miniflux 口令：

```sh
docker exec mfdb psql -U miniflux -d miniflux \
  -c "UPDATE users SET password=crypt('<新口令>', gen_salt('bf')) WHERE username='radar';"
```

ntfy 换 token：

```sh
docker exec ntfy ntfy token add radar          # 取新 token
docker exec ntfy ntfy token revoke <旧token前缀>
```
