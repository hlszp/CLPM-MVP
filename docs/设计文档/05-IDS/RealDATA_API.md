# CLPM 实时数据 SignalR 对接文档

本模块提供一个 SignalR Hub，用于 CLPM 系统实时数据订阅与推送。

## 1. Hub 连接地址

- Hub URL: `/signalr/realValueForClpmHub`

示例：
```js
const connection = new signalR.HubConnectionBuilder()
  .withUrl("/signalr/realValueForClpmHub")
  .build();
```

## 2. Hub 方法

### 2.1 SubscribeAsync

- 调用名：`SubscribeAsync`
- 参数：`string[] tagCodes`
- 返回：`SignalRResponseDto<List<RealValueDto>>`
- 功能：订阅指定标签的实时数据，并返回当前这些标签的实时值。

请求示例：
```json
["TAG001", "TAG002", "TAG003"]
```

成功响应示例：
```json
{
  "code": 200,
  "message": "success",
  "data": [
    {
      "id": 1001,
      "tagCode": "TAG001",
      "value": "12.5",
      "quality": 0,
      "collectTime": "2026-06-25T10:15:30"
    },
    {
      "id": 1002,
      "tagCode": "TAG002",
      "value": "34.8",
      "quality": 0,
      "collectTime": "2026-06-25T10:15:30"
    }
  ]
}
```

### 2.2 UnsubscribeAsync

- 调用名：`UnsubscribeAsync`
- 参数：`string[] tagCodes`
- 返回：`SignalRResponseDto`
- 功能：取消当前连接的指定标签订阅。

请求示例：
```json
["TAG001", "TAG002"]
```

成功响应示例：
```json
{
  "code": 200,
  "message": "success"
}
```

### 2.3 UnsubscribeAllAsync

- 调用名：`UnsubscribeAllAsync`
- 参数：无
- 返回：`SignalRResponseDto`
- 功能：取消当前连接的全部标签订阅。

成功响应示例：
```json
{
  "code": 200,
  "message": "success"
}
```



## 3. 服务端推送事件

### 3.1 updateRealValues

- 推送事件名：`updateRealValues`
- 功能：当服务端检测到实时值变化时，按订阅关系向客户端推送对应标签的更新数据。

推送数据格式：
```json
[
  {
    "id": 1001,
    "tagCode": "TAG001",
    "value": "12.9",
    "quality": 0,
    "collectTime": "2026-06-25T10:18:05"
  },
  {
    "id": 1003,
    "tagCode": "TAG003",
    "value": "78.2",
    "quality": 1,
    "collectTime": "2026-06-25T10:18:06"
  }
]
```

## 4. JavaScript 客户端示例

```js
const connection = new signalR.HubConnectionBuilder()
  .withUrl("/signalr/realValueForClpmHub")
  .build();

connection.on("updateRealValues", (data) => {
  console.log("收到实时值推送：", data);
});

async function start() {
  await connection.start();
  console.log("SignalR 已连接");

  const subscribeResult = await connection.invoke("SubscribeAsync", ["TAG001", "TAG002"]);
  console.log("订阅结果：", subscribeResult);
}

async function unsubscribe(tagCodes) {
  const result = await connection.invoke("UnsubscribeAsync", tagCodes);
  console.log("取消订阅结果：", result);
}

async function unsubscribeAll() {
  const result = await connection.invoke("UnsubscribeAllAsync");
  console.log("取消所有订阅：", result);
}

start().catch(console.error);
```
