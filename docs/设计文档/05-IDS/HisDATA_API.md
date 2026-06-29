# HistoryDataAppService 接口文档

## 接口：GetAsync

- 请求地址：/api/services/v1/HistoryData/Get
- 描述：获取历史数据采样结果，按固定时间间隔对指定标签集合进行时间序列采样。


### 请求参数

```json
{
  "tagCodes": ["TAG001", "TAG002"],
  "startTime": "2026-06-25T08:00:00",
  "endTime": "2026-06-25T09:00:00",
  "sampleInterval": 1
}
```

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `tagCodes` | `string[]` | 要查询的标签编码数组。 |
| `startTime` | `DateTime` | 查询开始时间。 |
| `endTime` | `DateTime` | 查询结束时间。 |
| `sampleInterval` | `int` | 采样间隔，单位为秒；默认值 `1`。 |

### 返回结果

```json
{
  "code": 200,
  "message": "Success",
  "data": {
    "timestamps": [
      "2026-06-25T08:00:00",
      "2026-06-25T08:01:00"
    ],
    "series": [
      {
        "tagCode": "TAG001",
        "values": ["12.345", "12.350"],
        "qualities": [192, 192]
      }
    ]
  }
}
```

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | `int` | 返回状态码，成功时一般为 `200`。 |
| `mssage` | `string` | 返回提示信息。 |
| `data` | `HistoryDataDto` | 数据体。 |
| `timestamps` | `List<DateTime>` | 采样时间点列表。 |
| `series` | `List<TagHistoryValueDto>` | 每个标签的历史值序列。 |
| `tagCode` | `string` | 标签编码。 |
| `values` | `List<string>` | 对应采样时间点的字符串值；无数据时为空字符串。 |
| `qualities` | `List<int>` | 对应采样时间点的数据质量码: 0: 未知，1：Good，2：Bad，3，离线。 |




