# 环境与协作流程

## Windows 三位成员

采用 WSL 2 Ubuntu。已在 haohao0803 的 Windows 上识别到 Ubuntu-24.04，WSL 版本为 2。工具和弱网能力按实测验证。

PowerShell 中查看环境并进入 Ubuntu。

```powershell
wsl --list --verbose
wsl -d Ubuntu-24.04
```

进入 Ubuntu 后安装工具，在 Linux 用户目录克隆。

```sh
sudo apt update
sudo apt install build-essential python3 git iproute2
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/haohao0803/network-programming.git
cd network-programming
make
make check
```

编辑器可以连接 WSL 目录，构建与运行命令在 Ubuntu 终端执行。

## Mac 成员 bushi510

2026-11-13 实际 Mac 构建、协议测试和真实收发验证已完成，详见
[日志与结果](progress/2026-11-13-mac.md)。本实验分支已修复严格 POSIX 模式下
Darwin 隐藏 `MSG_DONTWAIT` 的兼容问题，并统一两端构建入口。

安装 Apple Command Line Tools，确认 Python 3、Git、Clang 和 Make 可用。

```sh
xcode-select --install
clang --version
make --version
python3 --version
git clone https://github.com/haohao0803/network-programming.git
cd network-programming
make
make check
```

缺少 Python 3 时从 python.org 安装受支持版本。当前检查脚本使用 Python 标准库。

## Linux 弱网实验

bushi510 负责编排和统计，haohao0803 提供 Linux 环境配合。先验证 tc/netem、网络命名空间和权限。两端放在隔离命名空间，经 veth 通信，两方向分别配置条件，保存内核、接口、参数与实测结果。

若 WSL 内核缺少所需功能，改用具备这些能力的 Ubuntu 虚拟机。实验脚本负责创建、检查与恢复自己创建的环境，故障注入限定在实验接口。

跨机器联调先确认接收地址、防火墙和 WSL 网络模式，本机验证通过后再进行局域网测试。

## 2026-11-11 本机验证记录

haohao0803 的 WSL 2 Ubuntu-24.04 已有 cc、Python 3 和 tc，尚缺 make。安装前可直接验证示例。

```sh
mkdir -p build
cc -std=c11 -Wall -Wextra -Wpedantic -Werror -O2 src/udp_probe.c -o build/udp-probe
python3 scripts/check_probe.py build/udp-probe
```

实际验证发现该 WSL 的默认 127.0.0.1 路由经 loopback0，默认网络下 C 示例与独立 Python UDP 自收发均超时。在临时隔离网络命名空间中，编译通过，UDP 双向收发、非法端口和无响应超时检查通过。此次使用直接 cc 命令，make 入口待工具安装后验证，Mac 本机验证待 bushi510 完成。

在具备相应权限的 Ubuntu 中可用以下命令复现隔离检查。命名空间随命令退出释放。

```sh
sudo unshare --net sh -c 'ip link set lo up && python3 scripts/check_probe.py build/udp-probe'
```

## 分支与 PR

2026-11-13 已安装 Make，并在本机临时隔离网络中通过 `make clean`、`make check`。默认 WSL 网络问题尚未修复；这次结果不代表默认网络、Mac 或远端服务器验证通过。详见 [开发记录与日志](progress/2026-11-13.md)。

| 成员 | 功能分支建议 |
| --- | --- |
| haohao0803 | feature/sender |
| Clive-111 | feature/receiver |
| LPCAQ | feature/congestion-control |
| bushi510 | feature/experiments |

以上分支在相应成员开始模块时创建。

```sh
git switch main
git pull --ff-only
git switch -c feature/sender
```

每人使用自己的 GitHub 身份提交实际成果。一次提交说明一个清晰变化，PR 写明实现内容、验证命令、结果和剩余问题。至少一位其他成员审阅，通过构建和必要检查后合并到 main。

公共协议与接口先讨论再改。实验记录保留失败样本。老师新要求到达后更新 PLAN.md 的功能、负责人和验收项。
