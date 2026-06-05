# 通达信涨停板自动更新

## 核心文件
- **脚本**: `C:/new_tdx/tdx_zt_update.js` (Node.js)
- **板块目录**: `C:/new_tdx/T0002/blocknew/`

## 数据源
- **同花顺API**: `https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool`
- 需要 Referer: `https://www.10jqka.com.cn/`
- 返回字段：code, name, is_again_limit(0=首板,1=连板), change_rate, limit_up_type, reason_type

## 板块文件映射
| 文件 | 板块 | 说明 |
|------|------|------|
| ZFB.blk | 昨封板 | 昨日所有涨停 |
| SB.blk | 首板 | 今日首次涨停(is_again=0) |
| SYLB.blk | 所有连板 | 昨天也涨停的 |
| 2LB.blk | 2连板+ | 昨天也涨停的 |
| 2J3.blk | 2进3 | 空(需优化) |
| zxg.blk | 自选 | 用户手动维护 |

## blk文件格式
每行7位：第1位=市场(1沪/0深/3北交所)，后6位=股票代码

## 注意事项
- 东方财富 push2ex API 返回 rc:102，不可用
- 同花顺API可用但需指定日期，非交易时间可能无数据
- 写入blk需要sandbox权限(dangerouslyDisableSandbox)
