# 发送端事件记录

haohao0803 的发送循环可选择写入 CSV，供联调排错和后续实验工具读取。该日志记录发送端事件；LPCAQ 的参数计算和 bushi510 的批量实验/图表仍由对应成员负责。

```sh
mkdir -p results
./build/rudp-send 127.0.0.1 19090 input.bin 1000 5 4 --trace results/run-001-sender.csv
```

`--trace CSV` 必须放在命令末尾，也可与默认计时/窗口参数一起使用。文件父目录须存在；CSV 路径须为新文件，拒绝覆盖原输入或已有实验记录。未指定时不生成日志。

## CSV 版本 1

| 字段 | 含义 |
| --- | --- |
| schema_version | 当前为 1 |
| event_index | 从 0 开始的行序号，同毫秒内按该字段排序 |
| transfer_id | 实际报文中的随机 32 位会话 ID，十进制 |
| elapsed_ms | 单调时钟记录的相对毫秒数，起点为网络阶段初始化 |
| event | 本地事件名，见下表 |
| packet_type | 报文类型值 1–7；本地 begin/complete/failed 为 0 |
| sequence | 事件涉及的报文序号；本地结束事件为当前 base |
| base / next | 事件后的最早未确认、首个尚未发送序号 |
| receive_window | 最近接受的有效窗口通告，单位为分片 |
| rto_ms | 当前固定超时配置，单位为毫秒；尚非自适应 RTO |
| configured_window | 本地配置上限，单位为分片；不是 cwnd |
| retransmissions / probes | 截至该事件的累计重传/探测次数，探测单独计数 |

| event | 何时记录 |
| --- | --- |
| begin | 初始化后、发送 START 前 |
| send / retransmit | 实际发送成功后；重传使用原始缓存报文 |
| control_ack | 接受有效 START_ACK 或 FIN_ACK |
| ack | 新累计确认推进 base 后 |
| window_update | 同一确认位置的有效窗口变化，包括探测恢复 |
| timeout | 一次等待到期；失败预算耗尽后由 failed 结束 |
| probe | 实际发送零窗口探测后 |
| peer_error | 接受当前会话相关的 ERROR |
| complete / failed | 两端完成确认成功 / 本次运行未成功结束 |

无效包和没有窗口变化的重复 ACK 不逐包写日志，以免无关流量产生大量磁盘写入。没有 FIN_ACK 时不会写 complete。CSV 内容来自本地状态和常量事件名，不直接写入远端错误文本或本地输入路径。

## 结果解释与限制

正常成功输出还包括 `elapsed_ms`，覆盖网络阶段及最后日志收尾，不将接收端 linger 等待时间当成发送端传输耗时。短文件在本地回环可能低于毫秒分辨率，不能据此计算有意义的吞吐量。

日志写入或关闭失败时程序返回 1，并明确区分接收完成是否已经确认；不会输出 PASS。日志创建失败返回 2，尚未发送任何报文。强制终止可能留下不完整 CSV；判断实验结果必须同时保存程序退出码、双方输出和文件哈希。

日志会产生额外 I/O，比较实验需统一是否启用。当前没有 RTT 样本、cwnd 或 ssthresh 数据，接入参数模块后共同定义版本扩展。批量实验还需记录提交号、文件大小/哈希、系统、网络条件、种子和运行 ID，这些由实验编排工具补充，不能仅凭此 CSV 宣称实验记录完整。

验证入口：`python3 tests/test_sender_trace.py ./build/rudp-send`。6 个测试方法覆盖事件/计数与实际结果对应、零窗口恢复、控制确认丢失、最终未确认、文件保护、日志写入失败和默认参数。
