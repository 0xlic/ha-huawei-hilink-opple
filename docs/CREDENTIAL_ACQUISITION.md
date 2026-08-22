# 从华为账号取得 Home Assistant 配置值

这份文档补全本项目最容易丢失、也最难重新探索的部分：从“灯已经绑定在华为智慧生活账号中”开始，怎样取得每盏灯的 `device ID` 和本地 `authCode`，再怎样证明它们分别属于哪个 IP。

仓库提供两种入口：

- 本地 Web 助手：适合普通用户，负责打开授权页、捕获回调、查询凭据和只读匹配 IP；
- 命令行工具：适合排错、审计和无法运行图形浏览器的环境。

## 结论先说

获取凭据本身不依赖手机上的智慧生活 App，也不要求 Android 无线调试。需要的是：

1. 灯已经绑定在登录账号的智慧生活家庭中；
2. 一台能运行 Python 和桌面浏览器的电脑；
3. 用户亲自在华为官方页面完成授权；
4. 电脑或可选的 Android 中继能通过 UDP `5686` 访问灯具，才能自动完成 IP 匹配。

手机 App 仍可能用于首次配网、解绑或固件升级。Android 无线调试只是在路由器隔离 IoT 客户端、电脑无法直连灯具时的可选网络中继，不参与华为账号登录。

## 最短操作路径：本地 Web 助手

### 1. 安装运行环境

要求 Python 3.11 或更高版本。进入仓库后创建独立环境：

```bash
python3.11 -m venv .venv-credential
source .venv-credential/bin/activate
python -m pip install 'cryptography>=41' 'playwright>=1.46'
```

如果电脑已经安装 Chrome，可直接使用它：

```bash
python -m tools.credential_web --browser-channel chrome
```

否则先安装 Playwright 自带的 Chromium：

```bash
python -m playwright install chromium
python -m tools.credential_web
```

工具只监听 `127.0.0.1` 的随机端口，并生成一个不可猜测的临时页面路径。终端会打印本地地址，同时自动打开页面。

### 2. 完成华为授权

1. 点击“打开华为授权页面”；
2. 在弹出的独立浏览器中检查域名确实属于 `huawei.com`；
3. 登录拥有这些灯的华为账号；
4. 同意设备与智慧生活技能范围的授权；
5. 等待本地页面显示查询结果。

登录表单直接由华为页面提供。本地工具看不到账号密码。它只监听浏览器最终尝试访问的 `hms://redirect_url`，从中取出一次性授权码并校验 `state`，随后立即换取查询所需的短期 token。

页面会显示：

- 智慧生活中的设备名称；
- 型号；
- `device ID`；
- 本地 `authCode`。

OAuth access/refresh token 不会显示、不会写文件，也不会进入 Web 服务器日志。`device ID` 和 `authCode` 暂存在工具进程内存中；点击“清除页面中的凭据”或按 `Ctrl-C` 退出即清空。

### 3. 把凭据和 IP 一一匹配

从路由器复制属于这些灯的 IP 地址，在页面中用空格或逗号分隔，然后点击“只读匹配 IP”。

对每个 IP，工具依次尝试每条候选凭据：

1. 与灯协商 HiLink 本地会话；
2. 完成 `.sysConfirm`；
3. 验证 HMAC 并解密响应；
4. 读取一次 `devDataInfo`；
5. 只有四步全部通过才写入对应 IP。

这个过程不会发送开关、亮度或色温控制命令。不要按照云端列表顺序、房间名或路由器备注猜测对应关系。

匹配成功后，页面表格就是 HA 配置流程需要的主要内容：名称、IP、型号、`device ID`、`authCode`。

### 4. 路由器隔离导致匹配失败时

先判断错误类型：如果所有凭据对所有 IP 都超时，更像网络隔离；如果能收到会话响应但全部鉴权失败，才优先怀疑凭据或固件不兼容。

有一台已通过 Android 无线调试连接的手机，并且手机能访问灯具时，可以只把 UDP 探测经手机转发：

```bash
adb pair <手机显示的配对地址>
adb connect <手机显示的调试地址>
adb devices
python -m tools.credential_web --browser-channel chrome --adb-serial <adb序列号>
```

手机中继只使用 Android 自带 Toybox 的 UDP `nc`，每个数据报使用唯一临时文件，结束后立即删除。华为账号授权仍在电脑浏览器中完成。

## HA 字段分别从哪里来

| HA 配置项 | 来源 | 是否能自动取得 |
| --- | --- | --- |
| 名称 | 用户自定，或沿用智慧生活名称 | Web 助手显示建议值 |
| IP 地址 | 路由器 DHCP/客户端列表 | 用户粘贴后可只读匹配 |
| 型号 | 华为设备列表 | 自动 |
| 华为设备 ID | 华为设备列表 | 自动 |
| 本地 `authCode` | 华为本地控制授权接口 | 自动 |

MAC 地址只用于在路由器中辨认设备和设置 DHCP 地址保留，不需要填入 HA。

## 工具内部实际执行了什么

下面是本次研究中走通并固化到代码里的完整云端链路。它属于兼容性实现，不是华为承诺稳定的第三方开放 API。

### 1. HMS Lite 交互式授权

静态分析确认智慧生活使用：

- 应用 ID：`10406921`；
- 回调：`hms://redirect_url`；
- `response_type=code`；
- `access_type=offline`；
- PKCE `S256`；
- 账号基础、智慧生活设备、智慧生活技能三个业务范围，外加 `openid`。

Web 助手为每次运行生成新的 `state`、`nonce`、PKCE 值和 UUID，打开华为 OAuth v3 授权页。浏览器自动化只负责观察最终自定义协议回调，绝不读取表单内容或华为页面 DOM。

### 2. 一次性 code 换短期 token

工具把授权码通过以下兼容端点交换：

```text
POST /smart-life/v2/hms-lite/token
body: appId + one-time code
```

token 只存在于当前 Python 调用栈中。错误响应可能回显账号或设备信息，所以工具只打印阶段名与 HTTP 状态，不打印响应正文。

### 3. 查询家庭和设备

```text
GET /userApp/v1.2.0/homes
GET /smart-life/v2/devices        header: x-homeId
GET /smart-life/v3/devices        query: homeId
```

V2 和 V3 返回外形并不总是一致，部分字段还是字符串化 JSON。解析器会递归遍历两种响应、按设备 ID 去重，再优先选择产品 ID `2BB0`、`2BB2` 或型号 `MX420-D24-WTT`、`MX480-D48-WTT`。

如果设备响应省略产品元数据，工具不会据此断言“不支持”，而是把设备列表交给本地授权接口；该接口自身会筛掉不具备本地 HiLink 凭据的设备。

### 4. 查询本地控制授权

```text
POST /home-manager/v1/homes/devices/authcode
body: devIds[]
```

返回的 `authCode` 不是网页登录密码、OAuth code 或 access token。它是灯具本地会话的长期共享材料，与 `device ID` 一起用于局域网协议鉴权。

### 5. 在局域网证明映射关系

云端返回记录后，工具不发送任何写操作。它使用每个候选组合完成：

```text
IP + device ID + authCode
  -> UDP 5686 / CoAP
  -> /.sys/sessMngr
  -> PBKDF2 派生 AES/HMAC 密钥
  -> .sysConfirm
  -> devDataInfo {"type":"allSevice"}
```

错误凭据无法同时通过确认、HMAC、解密和状态解析，因此成功的只读请求就是“这个 IP 对应这条记录”的密码学证据。

## 上午实际探索过程的复盘

这部分记录为什么最后会走到上面的路径，避免以后只剩一份能运行却无法维护的代码。

### 第一步：先建立物理设备与网络地址清单

在路由器中记录灯具 IP、MAC、上下线时间，再通过单盏断电/上电确认房间。此前发生过“主卧和次卧反了”，根因就是人在房间里的判断错误，而不是 IP 或 MAC 失效。这说明房间名只能当标签，协议验证才是最终依据。

### 第二步：保留 App 日志，但不期待日志直接吐出密钥

从手机完整复制 `Android/data/com.huawei.smarthome/files/Log`，用来确认应用版本、服务名称、接口族和本地控制行为。日志没有稳定、可安全复用的明文凭据；它的价值是缩小搜索范围，而不是作为最终取密钥方案。

### 第三步：静态分析官方 App，定位正确授权链

从安装包中确认以下证据：

- Manifest 和应用配置给出智慧生活应用 ID；
- 资源字符串给出 devices/skill scopes；
- `HuaWeiIdSignInClient.Builder` 调用了 `setAppId`、`setRedirectUri`、`setScopes` 和 `requestAuthCode`；
- 智慧生活登录代码设置回调 `hms://redirect_url`；
- Huawei ID SDK 代码负责生成 state、nonce、PKCE 和授权 URL；
- 回调对象接收一次性 authorization code。

这一步把问题从“找 App 私有数据库或碰运气抓日志”转成了“复现用户可见的官方授权，再调用同一应用后端”。

### 第四步：在华为官方页面由用户亲自授权

研究时打开的是官方 OAuth 页面，用户自行登录并授权。浏览器最终导航到自定义协议回调；我们只截取其中一次性的 code，未保存账号密码、cookie 或 access/refresh token。

### 第五步：沿 App 的真实接口顺序取得本地凭据

先交换 token，再查 homes，然后同时查 V2/V3 devices，最后按设备 ID 请求 authcode。每步只输出成功阶段和记录数量。凭据文件以 `0600` 权限原子写入临时研究目录，从未进入发布仓库。

### 第六步：用只读请求逐灯匹配

云端设备顺序不足以证明房间关系，所以针对客厅测试灯逐条尝试。电脑所在网络最初无法直接收到 IoT 客户端的 UDP 回包；手机和灯处于可互访路径，因此临时使用 ADB + Toybox `nc` 转发单个数据报。匹配成功后才把同一方法用于其他灯。

### 第七步：状态读取稳定后才加入控制和 HA

先实现会话、HMAC、解密和 `devDataInfo`，连续读到真实状态后才加入开关、亮度和色温写入，最后封装配置流程与协调器。控制错序导致预设覆盖的问题也是在“写后立即回读”的验证中发现的。

## 命令行备用方案

### 已经有一次性授权码

不要把授权码写在命令行参数中，因为 shell history 会保留它。工具使用隐藏输入：

```bash
python -m tools.fetch_huawei_credentials \
  --acknowledge-unsupported-api \
  --output /安全目录/huawei_hilink_credentials.json
```

### 从命令行启动授权

```bash
python -m tools.fetch_huawei_credentials \
  --start-authorization \
  --acknowledge-unsupported-api \
  --output /安全目录/huawei_hilink_credentials.json
```

它会打印华为授权地址，再以隐藏输入接收最终 `hms://` 回调。输出文件以 `0600` 创建，不包含 OAuth token，但仍包含可控制灯具的本地凭据。

### 对单个 IP 做只读匹配

```bash
python -m tools.match_hilink_credentials 192.168.x.10 \
  --credentials-file /安全目录/huawei_hilink_credentials.json
```

输出只包含匹配记录序号和标准化状态，不打印设备 ID、`authCode` 或原始响应。网络隔离时可追加 `--adb-serial <序列号>`。

## 灾难恢复和凭据生命周期

### NAS 或 HA 重装

如果原来的 IP、设备绑定、`device ID` 和 `authCode` 都没变，直接从密码管理器取出五项配置重新添加即可，不必再次登录华为云。

完全没有备份时，重新运行 Web 助手即可：登录同一华为账号、重新查询、粘贴当前 IP、只读匹配，然后添加到 HA。不需要重新研究协议。

### 什么情况下应重新获取

`device ID` 通常跟随云端设备记录，不会因为 DHCP 地址或 HA 重装而变化。解绑后重新配网、换控制盒、恢复出厂后重新注册，可能生成新记录。

`authCode` 没有在协议中携带可见过期时间，正常断电、路由器重启和 HA 重装不会让它自动过期。解绑重绑、恢复出厂、厂商刷新本地授权或固件策略变化时，应视为可能改变。最可靠的判断永远是能否重新建立会话并完成只读状态请求。

## 兼容性边界

本流程复现的是智慧生活 `17.0.3.320` 中观察到的 HMS Lite 行为。应用 ID 不是用户秘密，但相关 OAuth 参数、域名和后端路径不是面向本项目承诺稳定的公共接口。华为可能随时改变授权要求、API 版本或限制客户端身份。

如果授权页不接受当前 client/redirect/scopes，不要绕过账号安全机制。记录不含敏感值的错误阶段、智慧生活版本和地区，在自己的华为开发者应用获得相同 scopes 后再适配，或等待项目更新。

## 最小隐私规则

- 只在 `huawei.com` 官方页面输入账号和验证码；
- 不截图包含 callback、`device ID` 或 `authCode` 的页面；
- 不把凭据 JSON 放在仓库、网盘公开链接或 Issue；
- Web 助手仅绑定 loopback，不要修改成 `0.0.0.0`；
- 用完点击清除并终止进程；
- 需要长期保存时放入密码管理器或加密备份。
