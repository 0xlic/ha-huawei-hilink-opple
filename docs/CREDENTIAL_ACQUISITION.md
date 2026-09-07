# 从华为账号取得设备凭据

本项目把云端授权和局域网匹配分成两个阶段：

1. 电脑上的本地凭据助手只打开华为官方授权页、查询设备列表和本地控制凭据，并把整批结果复制为 JSON；
2. Home Assistant 收到粘贴的 JSON 后，在家中局域网按 IP 逐条执行只读验证，自动找出正确的 `device ID + authCode`。

因此，电脑可以在公司或其他外网环境运行，不需要访问家里的灯具。

## 运行本地凭据助手

要求 Python 3.11 或更高版本。进入仓库后运行：

```bash
python3.11 -m venv .venv-credential
source .venv-credential/bin/activate
python -m pip install 'cryptography>=41' 'playwright>=1.46'
python -m tools.credential_web --browser-channel chrome
```

如果没有安装 Chrome：

```bash
python -m playwright install chromium
python -m tools.credential_web
```

工具只监听 `127.0.0.1`。点击“打开华为授权页面”，在域名属于 `huawei.com` 的页面完成登录和授权。助手捕获最终的 `hms://redirect_url`，用其中的一次性 code 查询家庭、设备和本地 `authCode`。

结果出现后点击“复制 JSON”。助手不进行 IP 匹配，也不需要能访问灯具。OAuth access/refresh token 不显示、不写入文件；页面中的设备凭据只存在于助手进程内存，点击清除或退出程序即丢弃。

## 在 Home Assistant 中添加灯具

1. 打开 **设置 → 设备与服务 → 添加集成**；
2. 搜索 **Huawei HiLink Opple Light**；
3. 第一次添加时，将助手复制的完整 JSON 粘贴到文本框并提交；
4. 输入灯具名称和路由器中看到的固定 IP；
5. HA 会用尚未添加的候选凭据依次建立本地会话并读取一次状态；成功后保存对应的设备 ID、`authCode` 和型号。

匹配只调用读取状态，不发送开关、亮度或色温命令。Home Assistant 必须能访问灯具的 UDP `5686` 端口。

导入的整批凭据会在 HA 运行内存中缓存 10 分钟。继续添加下一盏灯时，再次启动添加集成会直接显示“名称 + IP”表单，不必重新登录或粘贴 JSON。已添加设备的 ID 会从候选列表中排除，同一设备不能重复添加。

如果要换账号或重新获取更多设备，在“名称 + IP”页面勾选“清除临时凭据并返回 JSON 导入”，然后提交。缓存也会在以下情况自动消失：

- 导入 10 分钟后；
- Home Assistant 重启；
- 用户手动清除。

HA 只缓存 JSON 中的设备 ID、本地 `authCode`、型号和可选名称；其他字段会被忽略。成功匹配的那一盏灯的配置会正常写入 HA 配置条目，以便重启后继续控制。

## 命令行备用方式

已有一次性授权码时，可用隐藏输入生成 JSON 文件：

```bash
python -m tools.fetch_huawei_credentials \
  --acknowledge-unsupported-api \
  --output /安全目录/huawei_hilink_credentials.json
```

也可以由命令行生成授权地址：

```bash
python -m tools.fetch_huawei_credentials \
  --start-authorization \
  --acknowledge-unsupported-api \
  --output /安全目录/huawei_hilink_credentials.json
```

生成文件权限为 `0600`，不包含 OAuth token，但包含能控制灯具的本地凭据。复制文件内容并粘贴到 HA 的 JSON 文本框即可。

`tools.match_hilink_credentials` 保留为开发排错工具；正常添加流程不再使用它。

## 云端兼容链路

助手复现智慧生活现有的 HMS Lite 行为：OAuth v3 授权、短期 token、家庭查询、V2/V3 设备查询和 authcode 查询。这不是华为承诺稳定的第三方开放接口，华为服务或应用版本变化后可能需要适配。

请只在华为官方域名输入账号和验证码，不要把回调链接、设备 ID、`authCode` 或凭据 JSON 发到 Issue、公开聊天或日志中。
