# CloudForge 认证模块重构计划

## 📊 现状分析

### 现有两个认证模块

#### 1. `auth_api/auth_core.py` - 基础 JWT 认证
**技术栈**: python-jose + bcrypt

**优点**:
- ✅ 标准的 JWT 实现（RS256/HS256）
- ✅ Access token (15 分钟) + Refresh token (7 天)
- ✅ JTI 标识支持单 token 撤销
- ✅ Password hashing 使用成熟的 bcrypt

**缺点**:
- ❌ 密钥每次启动重新生成（未持久化）
- ❌ Refresh token 未实现 rotating 机制
- ❌ 缺少速率限制
- ❌ 依赖第三方库（python-jose）

#### 2. `src/openakita/api/auth.py` - Web 访问认证
**技术栈**: 标准库零依赖（hmac+hashlib+jwt）

**优点**:
- ✅ 零外部依赖
- ✅ Scrypt 密码哈希（比 bcrypt 更安全）
- ✅ Token version 机制支持批量撤销
- ✅ 本地请求免认证逻辑
- ✅ 内置速率限制器
- ✅ Access token (24h) + Refresh token (90 天) 可配置

**缺点**:
- ❌ 自研 JWT 实现，维护成本高
- ❌ Access token 过期时间固定，不够灵活
- ❌ 缺少 per-token 撤销能力（只有全局 version）
- ❌ 无 JTI 标识

---

## 🎯 重构目标

### 统一认证架构
合并两个模块的优点，创建统一的认证系统：

1. **核心层** (`core/auth/`):
   - 零依赖 JWT 实现（保留 auth.py 的优点）
   - 支持多种算法（HS256/RS256 可选）
   - 灵活的 token 过期策略

2. **业务层** (`api/auth_routes.py`):
   - 统一的登录/刷新/撤销接口
   - 速率限制
   - 审计日志

3. **存储层** (`storage/token_store.py`):
   - Redis 存储 revoked tokens
   - 支持 JTI + Version 双重机制
   - 自动过期清理

---

## 🔧 新 JWT 方案设计

### Token 结构
```python
# Access Token
{
    "sub": "user_id",
    "iat": 1234567890,
    "exp": 1234568790,      # 默认 30 分钟，可配置
    "jti": "unique-token-id",
    "type": "access",
    "scope": ["read", "write"]
}

# Refresh Token
{
    "sub": "user_id",
    "iat": 1234567890,
    "exp": 1242567890,      # 默认 30 天
    "jti": "unique-token-id",
    "type": "refresh",
    "version": 5            # 用于批量撤销
}
```

### 安全机制

1. **Rotating Refresh Token**:
   - 每次使用 refresh token 时生成新的 pair
   - 旧 token 加入 revocation list
   - 检测到重放攻击时撤销整个 family

2. **双重撤销机制**:
   - JTI: 单 token 精确撤销
   - Version: 用户级批量撤销（登出所有设备）

3. **密钥管理**:
   - HS256: 从环境变量加载（支持热更新）
   - RS256: 支持 JWKS 端点（多服务场景）

---

## ⚙️ Token 刷新机制实现

### API 设计
```python
POST /api/auth/refresh
{
    "refresh_token": "eyJ..."
}

Response:
{
    "access_token": "eyJ...",
    "refresh_token": "eyJ...",  # 新的 refresh token
    "expires_in": 1800
}
```

### 实现要点
1. 验证 refresh token 签名和过期时间
2. 检查 JTI 是否在 revocation list
3. 检查 version 是否匹配（防批量撤销）
4. 生成新的 access + refresh token pair
5. 将旧 refresh token 加入 revocation list
6. 返回新 token pair

---

## 🧪 测试策略

### 单元测试覆盖
- [ ] Token 生成/验证逻辑
- [ ] 签名算法正确性
- [ ] 过期时间边界条件
- [ ] Revocation list 操作
- [ ] Rotating 机制
- [ ] 并发场景下的 race condition

### 集成测试场景
- [ ] 完整登录流程
- [ ] Token 刷新流程
- [ ] 批量登出（version 递增）
- [ ] 单 token 撤销
- [ ] 速率限制触发
- [ ] 多设备同时登录
- [ ] Token 过期自动刷新

---

## 📁 文件结构
```
src/openakita/
├── core/
│   └── auth/
│       ├── jwt.py              # JWT 核心实现
│       ├── password.py         # 密码哈希（scrypt/bcrypt）
│       └── token_store.py      # Token 存储抽象
├── api/
│   └── auth_routes.py          # HTTP 接口
├── storage/
│   └── redis_token_store.py    # Redis 实现
tests/
├── unit/
│   └── test_auth.py
└── integration/
    └── test_auth_flow.py
```

---

## 🚀 迁移计划

### Phase 1: 并行运行（1 周）
- 新认证模块与旧模块共存
- 新注册用户使用新系统
- 老用户保持兼容

### Phase 2: 渐进迁移（2 周）
- 老用户登录时自动迁移到新系统
- 监控错误率和用户反馈

### Phase 3: 清理下线（1 周）
- 移除旧认证模块代码
- 清理数据库中的旧 token
- 更新文档

---

## ⚠️ 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 迁移期间用户无法登录 | 高 | 灰度发布，快速回滚 |
| Token 泄露 | 高 | 短有效期 + rotating + 审计日志 |
| Redis 单点故障 | 中 | Redis Cluster + 本地缓存降级 |
| 性能下降 | 中 | 基准测试 + 性能优化 |

---

## 📝 下一步行动

1. ✅ 分析现有认证流程（已完成）
2. ✅ 设计新的 JWT 方案（已完成）
3. ⬜ 实现 token 刷新机制
4. ⬜ 编写单元测试
5. ⬜ 集成测试

---

*文档创建时间：2026-04-02*  
*负责人：张伟（CloudForge 后端团队）*
