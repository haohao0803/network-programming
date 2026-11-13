# Network Programming

《网络编程》期末课程项目，澳门科技大学四人本科小组。

**基于 UDP 的可靠文件传输协议设计与弱网性能评估**

Reliable UDP File Transfer and Network Performance Evaluation

## 当前状态

`integration/file-transfer` 整合 haohao0803 的发送端与 Clive-111 的正式接收端，支持停止等待、固定滑动窗口、累计 ACK、超时回退重传、乱序缓存、去重、文件保存和零窗口恢复。构建入口统一提供两端，发送端可记录 CSV 事件。

两端真实文件联调已复跑；本整合版本的验证见 [集成记录](docs/progress/2026-11-13-integration.md)。自适应超时、拥塞控制、Mac/实际服务器验证及性能实验仍待完成。目标版本继续由组员审阅后再合入 main。

目标申报 1.40 档，最终由老师认定。已收到用户提供的汇报要求截图，落实方式见 [汇报要求记录](docs/PRESENTATION.md)；正式截止时间仍待确认。

**内部截止为 2026-11-17 23:59，时区 Asia/Shanghai，2026-11-18 可演示。** 老师正式截止时间待确认。

## 第一版目标

- 自定义报文、文件分片、ACK、超时重传、乱序重组、重复包处理。
- 滑动窗口、有限接收缓存与接收端流量控制。
- RTT 估计、自适应重传超时、基础慢启动、拥塞避免和丢包后的窗口收缩。
- 结束确认重试、有限失败等待、明确的成功或失败结果。
- SHA-256 文件一致性验证与弱网性能评估。

## 四人分工

| 成员 | 环境 | 主责 |
| --- | --- | --- |
| [haohao0803](https://github.com/haohao0803) | Windows / WSL 2 Ubuntu | 发送端、发送窗口、ACK 处理、重传调度、项目集成 |
| [Clive-111](https://github.com/Clive-111) | Windows / WSL 2 Ubuntu | 接收端、乱序缓存、去重、文件重组、流量控制 |
| [LPCAQ](https://github.com/LPCAQ) | Windows / WSL 2 Ubuntu | RTT/RTO、拥塞控制、参数日志 |
| [bushi510](https://github.com/bushi510) | macOS | 测试工具、实验自动化、图表、Mac 兼容性验证 |

协议由全组确认。弱网实验由 bushi510 编排，在验证通过的 Linux 环境执行，haohao0803 配合提供环境。

## 开始开发

核心计划采用 C11、POSIX Socket 和 Make，实验工具使用 Python 3。Windows 在 WSL 2 Ubuntu 内操作，Mac 使用 Command Line Tools 的编译工具。

```sh
git clone --branch integration/file-transfer https://github.com/haohao0803/network-programming.git
cd network-programming
make
make check
# 真实 C 两端的故障注入联调，约需一分钟
make check-integration
```

手动运行时，先在一个终端启动接收端，再在另一终端发送。

```sh
./build/udp-probe listen 19090
./build/udp-probe send 127.0.0.1 19090
```

当前探测示例只验证本机 UDP 双向收发，监听 127.0.0.1，等待上限 10 秒，接收一次后退出。

真实文件传输时，先在终端 A 启动接收端（目标文件须尚未存在），再在终端 B 发送：

```sh
# 终端 A
mkdir -p outputs
./build/rudp-recv 127.0.0.1 19090 outputs/received.bin
# 终端 B，input.bin 替换为准备好的普通文件
./build/rudp-send 127.0.0.1 19090 input.bin 1000 5 4
sha256sum input.bin outputs/received.bin
# macOS 用 shasum -a 256 替代 sha256sum
```

接收端收到完整数据并保存成功后返回 FIN_ACK，继续保留默认 7 秒的完成状态。发送端窗口参数省略时为 1；可在末尾加 `--trace results/NEW_RUN.csv`，先创建 results 目录并使用新文件名。完整说明见 [整合运行指南](docs/INTEGRATION.md)。

发送端仅支持数字 IPv4 和不超过 1 GiB 的普通文件。传输期间保持源文件不变。退出码 0 表示获得完成确认且所请求日志保存成功，1 表示传输或日志运行错误（stderr 区分是否已获得完成确认），2 表示参数、输入文件或日志创建错误。窗口上限为 1–256 个分片，缓存按上限分配，接收窗口进一步限制发送范围。默认超时 1 秒、额外重试 5 次；新累计确认会重置连续失败预算，重复 ACK 和窗口更新不延长等待。零窗口期间使用重复 START 或已确认 DATA 探测，连续无进展后失败。

当前为固定超时、固定窗口的实验基线；LPCAQ 的计时与拥塞模块接入后，发送上限还会受 cwnd 约束。完整行为及接收端配合要求见 [协议草案](docs/PROTOCOL.md)。

## 开发资料

- [LPCAQ 自适应 RTT/RTO、拥塞接口、单位与参数日志](docs/CONGESTION.md)：`--adaptive` 启用，`make check-adaptive` 验证；固定模式保留用于对照。
- [整合构建、运行与验证](docs/INTEGRATION.md)
- [发送端 CSV 事件说明](docs/SENDER_TRACE.md)
- [Clive 接收端、运行命令与真实联调](docs/RECEIVER.md)
- [小型 Ubuntu / Debian 服务器部署](docs/SERVER.md)
- [分工、每日计划与验收](docs/PLAN.md)
- [协议草案与接口讨论](docs/PROTOCOL.md)
- [Windows / Mac 环境及协作流程](docs/DEVELOPMENT.md)

统一实验环境计划使用组员提供的 Linux 服务器。服务器所有者按部署说明安装工具，并执行默认网络与隔离网络检查。各成员仍可在 WSL / Mac 开发和验证自己的模块。

## 参考资料

- [Microsoft WSL 版本说明](https://learn.microsoft.com/en-us/windows/wsl/compare-versions)
- [IETF UDP 使用指南 RFC 8085](https://www.rfc-editor.org/rfc/rfc8085.html)
- [Linux netem 手册](https://man7.org/linux/man-pages/man8/tc-netem.8.html)
