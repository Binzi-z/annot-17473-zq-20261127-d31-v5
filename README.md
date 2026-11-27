# 合成沙箱动作协议的状态对照

题面见 task/PROMPT.txt。仓库附本地合成输入及其格式检查。

## 性质

* 纯 Python 3.12 标准库（argparse / json / multiprocessing / dataclasses / enum
  / collections），离线运行，无任何真实工作区或网络操作；
* 动作仅为抽象编号（1..10，见 `protoctl/actions.py`），现场日志只记录
  合成动作号、结果类别编号与整数可见状态；
* 三类失败严格区分：缺边 `MISSING_EDGE`、无效状态 `INVALID_TARGET`、
  真正拒绝 `REJECTED`（`protoctl/lts.py`）；
* 三个活性性质互不坍缩：
  - EF：存在一条可完成必要清理的路径；
  - AG(EF)：每个可达局面都仍保留完成路径（违反时给出最小死端/闭环见证）；
  - AF：所有合法路径最终都会清理（最小不动点 μZ.(接受态 ∪ AX Z)）；
* 预算用完时结论明确标注 `unknown` / “仍未证”，绝不猜测。

## 保护语义（正确协议 P）

* 复用（动作 10）仅在“资源已按规定清理”且“归还原所有者”完成、
  且无残留读者引用时放行，否则真正拒绝；
* 第三并行读者持有抽象引用时，取消（动作 3）被真正拒绝，
  不能仅凭取消动作释放资源；
* 可重试虚拟资源数受配置预算限制，预算是隐藏内部状态，不出现在可见投影中。

## 用法

```powershell
# 双真实本地进程（spawn）+ 编号化工作区模拟器：最短分歧与最小不可清理环
python -m protoctl.cli demo --defect reject --retry-budget 1
python -m protoctl.cli demo --defect missing|invalid|shadow

# 小协议穷举全部可达状态，独立验证最短分歧的最短性
python -m protoctl.cli exhaust

# 同构分支规模夹具：等价状态压缩、搜索边界、预算耗尽未证
python -m protoctl.cli scale --depth 5 --branching 4
```

现场日志写入 `artifacts/`（已在 .gitignore 中忽略），
每个编号工作区一个 JSON 文件，行记录只含动作号与可见状态。

## 模块

| 模块 | 职责 |
| --- | --- |
| `protoctl/actions.py` | 抽象动作编号与可选动作开关 |
| `protoctl/states.py` | 内部状态、可见投影、复用双条件守卫 |
| `protoctl/lts.py` | 通用 LTS、三类失败、预算、可达 BFS |
| `protoctl/protocol.py` | 正确协议 P 与四类缺陷 Q（missing/invalid/reject/shadow） |
| `protoctl/engine.py` | 乘积 BFS 最短分歧、EF/AG(EF)/AF、Tarjan SCC、最小见证 |
| `protoctl/mini.py` | 小模型独立穷举（词树 + 独立不动点实现，交叉核对） |
| `protoctl/quotient.py` | 观察等价商压缩与同构分支规模夹具 |
| `protoctl/simulator.py` | 编号化工作区模拟器与双 spawn 进程编排 |
| `protoctl/cli.py` | 命令行入口 |

## 测试

```powershell
python -m unittest discover -s tests -v
```

45 个用例覆盖：三类失败、保护语义、最短性独立见证、三性质区分、
商压缩前后判定一致、预算未证、双进程 PID 独立性与日志格式。
