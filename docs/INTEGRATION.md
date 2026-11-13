# 两端整合指南

`integration/file-transfer` 包含 haohao0803 的发送端和 Clive-111 的接收端，保留原始作者提交。组员审阅后再合并到 main。Clive 的独立分支说明及历史验证保留在 RECEIVER.md；本页为整合分支的统一入口。

## 构建与检查

```sh
git clone --branch integration/file-transfer https://github.com/haohao0803/network-programming.git
cd network-programming
make
make check
make check-integration
```

`make` 构建 udp-probe、rudp-send、rudp-recv。`make check` 执行探测、发送端、窗口、日志及接收端检查。`make check-integration` 使用本目录两端程序执行真实落盘验证：Clive 的 31 个故障场景，加本人新增的停止等待/窗口日志联调。`make check-all` 顺序执行两个检查入口。

测试临时文件默认使用已有 TMPDIR，否则使用 POSIX `/tmp`。WSL 中优先使用 Linux 文件系统，避免 Windows 挂载目录对 FIFO 或并发日志读取的影响。可指定 `make TEST_TMPDIR=/dev/shm check-all`；编译产物和持久记录仍留在仓库中。直接运行 Python 测试时请单独设置 TMPDIR，Make 的导出设置只作用于 Make 启动的进程。

仍可用 `make check-integration SENDER=/absolute/path/to/reference/rudp-send` 比较独立构建的参考发送端；参考程序需支持整合测试用到的 `--trace` 参数。只验证旧参考版本时，直接运行 `tests/test_transfer.py`。

## 手动演示

准备普通输入文件，在两个终端使用相同项目目录：

```sh
mkdir -p outputs results
python3 -c 'from pathlib import Path; Path("results/input.bin").write_bytes(bytes(range(256))*4096)'
```

终端 A：

```sh
./build/rudp-recv 127.0.0.1 19090 outputs/received.bin
```

终端 B 在接收端等待超时前启动：

```sh
./build/rudp-send 127.0.0.1 19090 results/input.bin 1000 5 4 --trace results/demo-sender.csv
sha256sum results/input.bin outputs/received.bin
cmp results/input.bin outputs/received.bin
```

macOS 用 `shasum -a 256`。接收端默认等待上限为 15 秒，成功保存后保留完成状态 7 秒再退出。再次演示选择新的输出文件和 CSV 文件名。跨机器时，接收端使用可达的绑定地址，发送端替换为该地址；跨机器与 Mac 验证尚须单独完成。

本机 WSL 默认回环异常时，在临时隔离网络内执行 `sudo unshare --net sh -c 'ip link set lo up && make check-all'`。该方法只验证隔离的 Linux 网络，不修复默认 WSL 或 VPN，也不证明实际 VPS 已部署。

## 记录与剩余工作

集成原始结果可用以下环境变量保存，所指父目录由测试创建：

```sh
RUDP_RESULTS_JSON=results/integration.json RUDP_TRACE_RESULTS_DIR=results/traces make check-integration
```

记录双方退出码、源文件/真实输出哈希及故障计数；CSV 规则见 SENDER_TRACE.md。首次故障复现和修复环境也需保留，不把失败记录替换成只有通过的结论。

LPCAQ 的自适应 RTO/退避/cwnd 接入、bushi510 的批量性能实验与 Mac 验证、实际服务器检查和最终汇报仍待完成。本次整合不修改接收端 C 实现，测试包装和构建调整属于 haohao0803 的集成工作。
