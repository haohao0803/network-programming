# Network Programming

《网络编程》期末课程项目，澳门科技大学四人本科小组。

**基于 UDP 的可靠文件传输协议设计与弱网性能评估**

Reliable UDP File Transfer and Network Performance Evaluation

## 当前状态

本分支为 Clive-111 的 `feature/receiver`，提供可靠 UDP 接收端、乱序缓存、去重、文件保存和接收窗口控制。与 `feature/sender` 提交 `b3a4b45` 的联调方法见 [接收端说明](docs/RECEIVER.md)。整组自适应超时、拥塞控制、平台与实验验收按各模块实际进展确认。

目标申报 1.40 档，最终由老师认定。老师详细要求尚未发布，后续据此调整范围和验收标准。

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
git clone --branch feature/receiver https://github.com/haohao0803/network-programming.git
cd network-programming
make
make check
```

手动运行时，先在一个终端启动接收端，再在另一终端发送。

```sh
./build/udp-probe listen 19090
./build/udp-probe send 127.0.0.1 19090
```

上述 udp-probe 示例验证本机 UDP 双向收发，监听 127.0.0.1，等待上限 10 秒，接收一次后退出。真实文件传输使用 `build/rudp-recv`；编译、发送端工作目录、启动命令和 SHA-256 检查见 [接收端联调说明](docs/RECEIVER.md)。

## 开发资料

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
