# Huawei HiLink Opple Light for Home Assistant

一个非官方 Home Assistant 自定义集成，用于在局域网内控制华为智选 / 欧普 HiLink Wi-Fi 吸顶灯。

当前已实机验证：

- `MX420-D24-WTT`
- `MX480-D48-WTT`

## 功能

- 开灯与关灯
- 1–100% 亮度
- 2700–5700 K 色温
- 每 15 秒读取一次设备真实状态
- 会话失效或灯具重启后自动重新建立本地会话
- 多灯独立配置
- 保留华为智慧生活 App 的原有控制能力

所有日常控制均在 Home Assistant 与灯具之间的局域网内完成，不依赖云端转发。

## 前提

每盏灯需要以下信息：

1. 固定的局域网 IP 地址；
2. 华为设备 ID；
3. 与该设备对应的本地 `authCode`。

请只使用自己拥有或获授权管理的设备与账号。`device ID` 和 `authCode` 都应按凭据处理，不要提交到 Git、Issue、聊天记录或公开日志。仓库提供一个本地凭据助手，但不包含任何真实账号、令牌或设备数据。

## 获取 device ID 和 authCode

推荐使用仓库内的本地 Web 助手。它不依赖手机上的智慧生活 App：在桌面浏览器打开华为官方授权页，用户自行登录后，工具自动读取账号下可用于本地控制的灯具，并在仅限本机访问的页面中显示 HA 所需配置。

```bash
python3.11 -m venv .venv-credential
source .venv-credential/bin/activate
python -m pip install 'cryptography>=41' 'playwright>=1.46'
python -m tools.credential_web --browser-channel chrome
```

完整安装和授权过程见[凭据获取手册](docs/CREDENTIAL_ACQUISITION.md)。助手只取得并复制整批 JSON；局域网 IP 匹配由 Home Assistant 完成，因此电脑可以在外网运行。这是对智慧生活现有行为的非官方兼容实现，可能随华为服务变化。

## 安装

### 手动安装

将目录：

```text
custom_components/huawei_hilink_opple
```

复制到 Home Assistant 的：

```text
/config/custom_components/huawei_hilink_opple
```

重启 Home Assistant。

### 添加灯具

1. 在电脑上运行本地凭据助手，完成华为官方授权后点击“复制 JSON”；
2. 打开 **设置 → 设备与服务 → 添加集成**；
3. 搜索 **Huawei HiLink Opple Light**，粘贴完整 JSON；
4. 输入灯具名称和固定 IP；
5. HA 在家中局域网依次只读验证候选凭据，自动保存匹配成功的一条。

整批凭据在 HA 内存中缓存 10 分钟。继续添加集成会直接进入名称和 IP 表单；已添加的设备会自动排除。可在该表单勾选清除项并提交，立即删除缓存并返回 JSON 粘贴页。

## 网络要求

- Home Assistant 必须能够访问灯具的 UDP `5686` 端口；
- 建议在路由器中为每盏灯建立 DHCP 地址保留；
- 如果使用 IoT VLAN，需要允许 Home Assistant 到灯具的单向 UDP 访问及回包；
- 不需要开放公网端口。

## 工作方式

灯具使用旧版 HiLink 本地协议：CoAP 承载会话协商与业务请求，业务 JSON 经过 AES-CBC 加密，并用 HMAC-SHA256 校验。集成会在 Home Assistant 的执行器线程中完成同步 UDP 通信，通过协调器串行化同一盏灯的请求，并在会话失效时重新协商。

更详细的过程见：

- [研究与实现过程](docs/RESEARCH.md)
- [凭据获取与恢复手册](docs/CREDENTIAL_ACQUISITION.md)
- [协议说明](docs/PROTOCOL.md)
- [安全与隐私](docs/SECURITY.md)

## 已知限制

- HA 集成本身不会连接华为云；本地 Web 助手查询凭据，HA 只在局域网完成 IP 匹配；
- 仅验证了上面列出的华为智选欧普型号；
- 华为或欧普后续固件可能改变协议行为；
- Home Assistant 配置条目会保存本地控制凭据，应保护 `/config/.storage` 和备份文件。

## 开发检查

```bash
python -m compileall -q custom_components tests tools
python -m pytest
ruff check custom_components tests tools
```

## 声明

本项目与华为、欧普及 Home Assistant 官方均无隶属或授权关系。产品名称和商标归各自权利人所有。
