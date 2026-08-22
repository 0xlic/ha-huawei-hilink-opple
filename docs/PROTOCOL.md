# 协议说明

本页描述集成实现所需的协议子集，不包含任何真实设备标识或授权材料。

## 传输层

- 传输：UDP
- 端口：`5686`
- 上层格式：CoAP v1
- 请求方法：POST
- 内容格式：JSON（CoAP content-format `50`）

除标准 URI Path 和 Content Format 外，设备还使用高编号 CoAP 选项承载会话、请求、设备与序列信息。因此编码器必须正确实现 CoAP 扩展 delta/length。

## 会话协商

客户端向：

```text
/.sys/sessMngr
```

发送一次未加密 JSON 请求，包含：

- 随机数 `sn1`；
- 客户端请求序列；
- 支持的会话模式；
- 是否要求确认。

设备返回会话 ID、随机数 `sn2`、设备侧序列和协商模式。集成只接受已经实机验证的旧版模式。

## 密钥派生

设：

```text
salt = sn1 || sn2
```

则：

```text
digest   = PBKDF2-HMAC-SHA256(authCode, salt, iterations=1, length=32)
aes_key  = digest[0:16]
hmac_key = PBKDF2-HMAC-SHA256(aes_key, salt, iterations=1, length=32)
```

如果设备要求确认，客户端随后发送加密的 `.sysConfirm` 请求。设备 ID 参与协议选项与确认数据，但本文不展示任何实例值。

## 安全请求

每次业务请求都会生成新的：

- CoAP token；
- message ID；
- request ID；
- 16 字节 IV。

业务 JSON 使用 PKCS#7 填充和 AES-CBC 加密：

```text
secured_payload = AES_CBC(PKCS7(json), aes_key, iv) || iv
```

先构造包含 `secured_payload` 的完整 CoAP 报文，再计算：

```text
mac = HMAC-SHA256(hmac_key, coap_packet_without_mac)
```

最终负载为：

```text
ciphertext || iv || mac
```

响应必须先验证 HMAC，再解密。任何签名、填充或 JSON 失败都按鉴权失败处理，不使用未验证的数据更新 HA 状态。

## 状态读取

集成调用：

```text
service: devDataInfo
data: {"type":"allSevice"}
```

`allSevice` 是设备实际使用的拼写。解析器从服务列表提取：

- `switch.on`
- `brightness.brightness`
- `cct.colorTemperature`

亮度限制为 0–100，色温限制为 2700–5700 K。关闭状态下 HA 不显示亮度与色温，但协调器仍保留设备最后报告的属性。

## 控制服务

```text
switch       {"on": 0|1}
brightness   {"brightness": 1..100}
cct          {"colorTemperature": 2700..5700}
```

控制后立即读取状态，以设备回包作为最终结果。

## 序列与并发

协议会话包含双向序列。集成为每盏灯维护独立客户端，并用异步锁保证同一设备的状态轮询和控制不会并发修改序列。不同灯具之间互不共享会话或凭据。

## 失败恢复

以下情况会丢弃当前客户端并重新协商一次：

- UDP 超时；
- 会话过期；
- HMAC 或解密失败；
- 灯具断电重启。

第二次仍失败时，错误交给 HA `DataUpdateCoordinator`，实体会按 HA 的正常可用性机制处理。
