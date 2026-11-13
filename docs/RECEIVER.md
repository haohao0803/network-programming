# Clive 接收端与联调说明

接收端分支为 `feature/receiver`，作者身份为 Clive-111。本版本对接发送端提交 `b3a4b452c3510da9e13a0621b91dce9eec056097`，接口依据为该提交的 [PROTOCOL.md](https://github.com/haohao0803/network-programming/blob/b3a4b452c3510da9e13a0621b91dce9eec056097/docs/PROTOCOL.md)。协议细节供小组在 PR 中审阅。

## 获取与构建

Windows 使用 WSL 2 Ubuntu；以下命令在 Linux shell 执行。将仓库放在 D 盘挂载目录，如 `/mnt/d/web work`。

```sh
git clone --branch feature/receiver https://github.com/haohao0803/network-programming.git
cd network-programming
make
make check
```

已有克隆可执行 `git fetch origin`，然后切换至 `feature/receiver`。请先保存当前工作区中的修改。

## 接收端 CLI

```text
rudp-recv BIND_IPV4 PORT OUTPUT_FILE [WINDOW_PACKETS [IDLE_TIMEOUT_MS [LINGER_MS]]]
```

| 参数 | 约定 |
| --- | --- |
| BIND_IPV4 | 数字 IPv4；本机验证使用 127.0.0.1，跨机器使用接收机实际地址或 0.0.0.0 |
| PORT | 1–65535 的 UDP 端口 |
| OUTPUT_FILE | 接收机本地输出路径，父目录须存在，目标须尚未存在 |
| WINDOW_PACKETS | 1–256，默认 64，单位为分片 |
| IDLE_TIMEOUT_MS | 1–600000，默认 15000；等待首个有效 START 或等待累计接收进展的上限 |
| LINGER_MS | 1–600000，默认 7000；首次成功 FIN 后保留完成状态的时间 |

接收端一次处理一个发送端、一个文件。输出 `READY` 后开始接受报文；成功保留期结束后输出 `PASS receiver` 并返回 0。运行错误返回 1，参数错误返回 2。

发送端默认超时 1000 ms、额外重试 5 次，与默认 LINGER_MS=7000 配套。自定义参数时，保留期至少设为 `(RETRIES+1)*TIMEOUT_MS+1000`，无进展等待上限也应覆盖发送端的重试预算。测试中的较短参数会在各测试中配套设置。

## 真实文件收发

在接收端仓库中准备独立发送端工作目录和输入文件：

```sh
git worktree add --detach work/sender-reference b3a4b452c3510da9e13a0621b91dce9eec056097
make -C work/sender-reference
mkdir -p work outputs
python3 -c 'from pathlib import Path; Path("work/input.bin").write_bytes(bytes(range(256))*4096)'
```

终端 A 在接收端仓库运行：

```sh
./build/rudp-recv 127.0.0.1 19090 outputs/received.bin 64 15000 7000
```

终端 B 在相同目录运行，须在接收端无进展超时前启动发送：

```sh
./work/sender-reference/build/rudp-send 127.0.0.1 19090 work/input.bin 1000 5 4
sha256sum work/input.bin outputs/received.bin
cmp work/input.bin outputs/received.bin
```

发送端收到 FIN_ACK 后成功退出；接收端继续保留完成状态 7 秒，再成功退出。再次演示时使用新的输出路径。跨机器时，将发送命令中的地址替换为接收机可达的 IPv4，并确认 UDP 端口可达。

自动联调入口：

```sh
RUDP_RESULTS_JSON="$PWD/outputs/transfer-results.json" \
make check-integration SENDER="$PWD/work/sender-reference/build/rudp-send"
```

`make check` 验证 UDP 探测程序与接收端协议行为；`check-integration` 启动真实 C 发送端和接收端，转发器按固定规则施加故障。测试临时数据放在 `work/tmp`，测试结束清理。结果记录包含源文件和实际输出文件的 SHA-256。

## 接收状态与窗口

报文头 20 字节，字段逐项使用网络字节序编解码。DATA 载荷最多 1024 字节，文件上限 1 GiB。START 携带总分片数和文件字节数，接收端检查二者一致。

`base` 是下一未连续收到并写入的分片序号。缓存范围为 `[base, base+window)`，窗口通告值为配置容量和剩余分片数的较小值。乱序分片保存在环形缓存中，连续片到齐后按序写入；缓存占用上限始终为配置容量。窗口的含义是序号跨度。

相同源 IP、端口和 transfer_id 标识同一会话。元数据一致的重复 START 返回 START_ACK，已有文件进度保留；元数据冲突的重复 START 保持原会话。重复或窗口外 DATA 回复当前累计 ACK。提前 FIN 回复当前 ACK，收齐全部数据后处理 FIN。

### 零窗口控制

接收端进程支持两个 POSIX 信号：

```sh
kill -USR1 RECEIVER_PID   # 暂停新 DATA，通告窗口 0
kill -USR2 RECEIVER_PID   # 恢复接收并通告当前窗口
```

`RECEIVER_PID` 替换为本次接收进程 PID。状态生效后输出 `WINDOW paused=1/0 base=N window=N`。测试等待这条日志作为同步点。

暂停时保留已接收数据，继续回复重复 START、已确认 DATA 和在途 DATA。发送端通过重复 START（base=0）或已确认 DATA（base>0）探测窗口；恢复通告丢失后，后续探测仍可取得最新窗口。空文件和已收齐文件继续处理 FIN。

信号处理器仅记录期望状态；主循环在报文处理边界应用状态，单次 poll 最多等待 50 ms。重复包、无关包、窗口探测和控制信号保持原有无进展期限。持续暂停最终触发有界失败。

## 输出文件与失败语义

启动时在输出文件所在目录创建随机 `.rudp-recv-XXXXXX` 临时文件。完整数据收到后等待 FIN；成功写入、fsync 和 close 后，以同目录硬链接原子创建最终路径，再移除临时名称。目标路径在启动后被其他程序创建时，最终发布会拒绝覆盖并失败。

最终输出仅在完整保存后出现。常规运行错误、无进展超时和 SIGINT/SIGTERM 会尝试清理本次临时文件。强制终止或系统故障可能留下临时文件；此时可根据进程状态人工清理。已经成功发布的完整输出保留。

首次成功 FIN 触发固定完成保留期，重复 FIN 再次获得 FIN_ACK，保留期保持原截止时间。所有 FIN_ACK 丢失时，两端可能分别报告文件已保存和发送完成确认超时；联调记录需要同时保存两端结果。

## 验证范围

本次交付以 WSL 本机真实收发和可复现故障注入为验收范围。Mac、实际服务器、跨机器通信以及 Linux netem 性能实验由组内后续验证并单独记录。原始记录和个人进展随接收端 PR 提供。
